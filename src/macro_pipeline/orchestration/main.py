import structlog
from datetime import date
import pandas as pd

from macro_pipeline.data.fmp_client import FMPClient
from macro_pipeline.validators.schemas import WeeklyCloseData
from macro_pipeline.validators.engine import ValidationEngine
from macro_pipeline.llm.client import LLMClient
from macro_pipeline.llm.validator import ValidatorAgent
from macro_pipeline.render.playwright_engine import PlaywrightEngine
from macro_pipeline.telegram.bot import TelegramBot
from macro_pipeline.publishers.x_client import XClient
from macro_pipeline.publishers.linkedin_client import LinkedInClient
from macro_pipeline.storage.r2_client import R2Client
from macro_pipeline.storage.state import StateDB
from macro_pipeline.observability.logger import setup_observability

logger = structlog.get_logger(__name__)

class MacroOrchestrator:
    """
    Clase principal que coordina todos los módulos del MacroPipeline de forma secuencial
    y respetando la arquitectura de datos deterministas -> IA auxiliar -> HITL -> Redes.
    """
    def __init__(self, tracer=None):
        self.tracer = tracer
        logger.info("initializing_orchestrator")
        # 1. Capa de Datos y Validación
        self.fmp = FMPClient()
        self.validator_engine = ValidationEngine()
        
        # 2. Capa LLM y Renderizado
        self.llm = LLMClient()
        self.validator_agent = ValidatorAgent(self.llm)
        self.renderer = PlaywrightEngine()
        
        # 3. Capa de Seguridad (HITL) y Estado Local
        self.telegram = TelegramBot()
        self.state = StateDB()
        
        # 4. Capa de Almacenamiento Remoto
        try:
            self.r2 = R2Client()
            self.r2_ready = True
        except ValueError as e:
            logger.warning("r2_not_configured", reason=str(e))
            self.r2_ready = False
        
        # 5. Capa de Publicación (Manejo tolerante a fallos si faltan tokens de dev)
        try:
            self.x_client = XClient()
            self.linkedin = LinkedInClient()
            self.publishers_ready = True
        except ValueError as e:
            logger.warning("publishers_not_configured_for_publishing", reason=str(e))
            self.publishers_ready = False

    def _fetch_weekly_close(self) -> WeeklyCloseData:
        """Extrae y calcula los datos deterministas para el cierre semanal."""
        logger.info("orchestrator_fetching_data")
        
        # Obtener histórico usando Financial Modeling Prep
        sp500_df = self.fmp.get_historical_prices("^GSPC")
        nasdaq_df = self.fmp.get_historical_prices("^IXIC")
        
        if len(sp500_df) < 5 or len(nasdaq_df) < 5:
            raise ValueError("No hay suficientes datos históricos para calcular retornos semanales.")
            
        # Tomar el cierre actual y el de hace 5 días hábiles
        sp_last = sp500_df.iloc[-1]
        sp_prev = sp500_df.iloc[-6] 
        
        ndq_last = nasdaq_df.iloc[-1]
        ndq_prev = nasdaq_df.iloc[-6]
        
        sp_return = (sp_last['close'] - sp_prev['close']) / sp_prev['close']
        ndq_return = (ndq_last['close'] - ndq_prev['close']) / ndq_prev['close']
        
        # Forzar casteo a través del esquema Pydantic para tipado estricto
        return WeeklyCloseData(
            date=sp_last['date'].date() if isinstance(sp_last['date'], pd.Timestamp) else date.today(),
            sp500_close=float(sp_last['close']),
            sp500_weekly_return=float(sp_return),
            nasdaq_close=float(ndq_last['close']),
            nasdaq_weekly_return=float(ndq_return)
        )

    def run_weekly_close(self):
        """Pipeline completo de Cierre Semanal."""
        logger.info("starting_weekly_close_pipeline")
        
        # Envolver todo el pipeline en un único span distribuido
        span_context = self.tracer.start_as_current_span("weekly_close_pipeline") if self.tracer else None
        
        try:
            if span_context: span_context.__enter__()
            
            # 1. Generar ID de evento y revisar estado
            event_id = f"weekly_close_{date.today()}"
            if self.state.is_published(event_id):
                logger.info("event_already_published_skipping", event_id=event_id)
                return
            
            # --- FASE DE DATOS ---
            data = self._fetch_weekly_close()
            
            # --- FASE DE SANITY CHECKS ---
            self.validator_engine.validate_weekly_close(data)
            
            # --- FASE DE RENDERIZADO VISUAL ---
            image_bytes = self.renderer.render_weekly_close(data)
            
            # --- FASE DE INTELIGENCIA ARTIFICIAL ---
            data_str = (
                f"SP500: Cierre {data.sp500_close:,.2f} (Retorno Semanal: {data.sp500_weekly_return*100:+.2f}%)\n"
                f"NASDAQ: Cierre {data.nasdaq_close:,.2f} (Retorno Semanal: {data.nasdaq_weekly_return*100:+.2f}%)"
            )
            
            headline = self.llm.generate_headline(data_str)
            
            # Auditoría del titular
            review = self.validator_agent.review_draft(headline, data_str)
            if not review.get("approved"):
                logger.error("draft_rejected_by_ai", reason=review.get("reason"))
                # Fallback seguro determinista si el LLM alucina
                headline = f"📊 Cierre de Mercado Semanal:\nS&P500: {data.sp500_weekly_return*100:+.2f}%\nNASDAQ: {data.nasdaq_weekly_return*100:+.2f}%"
            
            # --- FASE HITL (HUMAN IN THE LOOP) ---
            logger.info("requesting_human_approval")
            msg_id = self.telegram.send_approval_request(text=headline, image_bytes=image_bytes)
            
            # Bloquear la ejecución esperando aprobación (hasta 1 hora)
            approved = self.telegram.wait_for_approval(msg_id, timeout_seconds=3600)
            
            # --- FASE DE PUBLICACIÓN ---
            if approved:
                logger.info("pipeline_approved_publishing")
                
                # Subir imagen a R2 si está disponible
                image_url = None
                if self.r2_ready:
                    image_url = self.r2.upload_image(image_bytes, f"{event_id}.png")
                
                if self.publishers_ready:
                    # MVP: Publica el texto. En v2 inyectaríamos image_url en el payload.
                    self.x_client.post_tweet(headline)
                    self.linkedin.post_text(headline)
                    logger.info("pipeline_published_to_socials")
                else:
                    logger.warning("pipeline_approved_but_publishers_missing")
                    
                # Guardar el éxito en SQLite para evitar duplicados en el futuro
                self.state.mark_as_published(event_id, image_url)
                logger.info("pipeline_completed_successfully")
            else:
                logger.warning("pipeline_aborted_by_human")
                
        except Exception as e:
            logger.error("pipeline_failed_critically", error=str(e))
            if span_context:
                span_context.record_exception(e)
            raise
        finally:
            if span_context: span_context.__exit__(None, None, None)

if __name__ == "__main__":
    import logging
    # La configuración básica de logging se delegó a observability/logger.py
    # structlog.configure(...) ya no es necesario aquí
    
    tracer = setup_observability()
    
    orchestrator = MacroOrchestrator(tracer=tracer)
    orchestrator.run_weekly_close()
