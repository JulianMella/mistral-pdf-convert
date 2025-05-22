# /// script
# requires-python = ">=3.8"
# dependencies = [
#   "fastapi==0.115.12",
#   "mangum",
#   "python-multipart",
#   "pisco-mistral-ocr @ git+https://github.com/JoaquinMulet/pisco-mistral-ocr.git",
#   # Para desarrollo local y ejecución directa con 'uv run':
#   "uvicorn[standard]==0.34.2",
#   "pytest"
# ]
# ///
"""
Backend FastAPI para un servicio de OCR de PDF y servicio de frontend.

Este servicio utiliza la librería PiscoMistralOcrClient para interactuar
con la API de Mistral AI, permitiendo a los usuarios subir archivos PDF
y obtener el texto extraído. La API key de Mistral es proporcionada
por el usuario en cada solicitud y no se almacena en el servidor.
Adicionalmente, sirve una interfaz de usuario frontend desde la carpeta 'front'.
La respuesta incluye datos estructurados por página, incluyendo imágenes opcionalmente.
"""

import logging
import os
import shutil
import tempfile
from typing import Dict, Any, List, Optional 
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

# Importaciones de terceros
from pisco_mistral_ocr import (
    ApiError as PiscoApiError,
    ConfigurationError as PiscoConfigurationError,
    FileError as PiscoFileError,
    NetworkError as PiscoNetworkError,
    PiscoMistralOcrClient,
    # OcrPage as PiscoOcrPage, # Ya no es estrictamente necesario aquí si no se usa para type hinting explícito
)

from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, # Nivel de log para producción
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# --- Constantes y Configuración Global ---
ALLOWED_ORIGINS: List[str] = [
    "http://localhost",
    "http://localhost:8000", # Uvicorn default
    "http://localhost:8080", # Otro puerto común de desarrollo
    "http://127.0.0.1:5500", # Live Server de VSCode
    # "https://tu-dominio-de-frontend.com", # DESCOMENTAR Y AJUSTAR PARA PRODUCCIÓN
]
OCR_CLIENT_TIMEOUT: float = 300.0 # 5 minutos

# --- Aplicación FastAPI ---
app = FastAPI(
    title="OCR PDF Service with Frontend",
    description=(
        "API para extraer texto de archivos PDF usando Mistral AI "
        "a través de PiscoMistralOcrClient, y servir una interfaz de usuario. "
        "Devuelve datos estructurados por página."
    ),
    version="1.2.0", # Versión actualizada
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# --- Manejadores de Excepciones Personalizados ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    error_messages = [err["msg"] for err in exc.errors()]
    primary_error_message = "Error de validación en la solicitud."
    if error_messages:
        for err_detail in exc.errors():
            loc = err_detail.get("loc", [])
            if "api_key" in loc:
                primary_error_message = "El campo 'api_key' es requerido o inválido."
                break
            if "pdf_file" in loc:
                primary_error_message = "El campo 'pdf_file' es requerido o inválido."
                break
        else:
            primary_error_message = error_messages[0]

    logger.warning(
        f"Error de validación de solicitud para {request.url.path}: "
        f"{primary_error_message}. Detalles: {exc.errors()}"
    )
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": primary_error_message},
    )

# --- Endpoints de la API ---
@app.post(
    "/api/ocr-pdf",
    summary="Procesa un archivo PDF para realizar OCR.",
    description=(
        "Recibe una API Key de Mistral AI, un archivo PDF y una opción para incluir imágenes. "
        "Realiza OCR y devuelve el texto y las imágenes (opcional) extraídos, estructurados por página."
    ),
    response_model=None, # Se define la respuesta explícitamente
    tags=["OCR"],
)
async def ocr_pdf_endpoint(
    api_key: str = Form(
        ...,
        description="API Key de Mistral AI proporcionada por el usuario.",
        min_length=1,
    ),
    pdf_file: UploadFile = File(
        ...,
        description="Archivo PDF a procesar."
    ),
    include_images: bool = Form(
        False,
        description="Indica si se deben incluir las imágenes extraídas del PDF en la respuesta."
    )
) -> JSONResponse:
    if not pdf_file.filename:
        logger.warning("Intento de subida de archivo PDF sin nombre.")
        raise HTTPException(status_code=400, detail="El archivo PDF debe tener un nombre.")

    if pdf_file.content_type != "application/pdf":
        logger.warning(
            f"Archivo '{pdf_file.filename}' subido con tipo de contenido "
            f"inesperado: '{pdf_file.content_type}'. Se intentará procesar."
        )

    tmp_file_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file_obj:
            shutil.copyfileobj(pdf_file.file, tmp_file_obj)
            tmp_file_path = tmp_file_obj.name

        api_key_suffix = api_key[-4:] if len(api_key) >= 4 else "..."
        logger.info(
            f"Archivo PDF '{pdf_file.filename}' guardado temporalmente en "
            f"'{tmp_file_path}'. API Key (parcial): ...{api_key_suffix}. "
            f"Incluir imágenes: {include_images}"
        )

        processed_pages_data: List[Dict[str, Any]] = []
        concatenated_markdown = ""

        async with PiscoMistralOcrClient(
            api_key=api_key, timeout=OCR_CLIENT_TIMEOUT
        ) as client:
            logger.info(
                f"Iniciando OCR para '{tmp_file_path}' con PiscoMistralOcrClient "
                f"(timeout: {OCR_CLIENT_TIMEOUT}s)."
            )
            ocr_result = await client.ocr(
                tmp_file_path,
                include_image_base64=include_images,
                delete_after_processing=True # Manteniendo tu default
            )
            logger.info(
                f"OCR completado para '{tmp_file_path}'. Archivo en Mistral "
                "eliminado (solicitado por delete_after_processing)."
            )

            if ocr_result and ocr_result.pages:
                all_page_markdowns: List[str] = []
                for page_idx, pisco_page in enumerate(ocr_result.pages):
                    page_data: Dict[str, Any] = {
                        "page_number": pisco_page.index if pisco_page.index is not None else page_idx,
                        "markdown": pisco_page.markdown or ""
                    }
                    all_page_markdowns.append(pisco_page.markdown or "")

                    if include_images and pisco_page.images:
                        page_data["images"] = []
                        for img_obj in pisco_page.images:
                            current_image_data = None
                            if isinstance(img_obj, dict):
                                base64_data_url_string = img_obj.get("image_base64")
                                
                                actual_base64_content = ""
                                detected_mime_type = "application/octet-stream" # Default

                                if base64_data_url_string and isinstance(base64_data_url_string, str) and base64_data_url_string.startswith('data:'):
                                    try:
                                        header, actual_base64_content = base64_data_url_string.split(',', 1)
                                        parts = header.split(';')
                                        if len(parts) > 0 and parts[0].startswith('data:'):
                                            mime_part = parts[0].split(':')
                                            if len(mime_part) > 1:
                                                detected_mime_type = mime_part[1]
                                    except ValueError:
                                        logger.warning(f"No se pudo parsear Data URL para imagen en página {page_idx}: {base64_data_url_string[:100]}...")
                                        # Si no se puede parsear pero no parece un Data URL, podría ser base64 puro
                                        if not ';' in base64_data_url_string and not ':' in base64_data_url_string:
                                            actual_base64_content = base64_data_url_string
                                        else: # No es base64 puro ni un Data URL parseable
                                            actual_base64_content = "" 
                                elif base64_data_url_string and isinstance(base64_data_url_string, str): # Asumir base64 puro si no es Data URL
                                    actual_base64_content = base64_data_url_string

                                if actual_base64_content:
                                    current_image_data = {
                                        "content_base64": actual_base64_content,
                                        "mime_type": detected_mime_type
                                    }
                                else:
                                    logger.warning(f"No se encontró 'image_base64' válido en el diccionario de imagen en página {page_idx}. Contenido parcial: {str(img_obj.get('image_base64'))[:100]}...")
                            
                            elif hasattr(img_obj, 'content_base64') and hasattr(img_obj, 'mime_type'):
                                # Caso para objetos Pydantic con atributos directos (menos probable con Mistral API)
                                current_image_data = {
                                    "content_base64": getattr(img_obj, 'content_base64'),
                                    "mime_type": getattr(img_obj, 'mime_type', "application/octet-stream")
                                }
                            elif isinstance(img_obj, str): # Fallback si es solo una string base64
                                current_image_data = {
                                    "content_base64": img_obj,
                                    "mime_type": "application/octet-stream"
                                }
                            
                            if current_image_data:
                                page_data["images"].append(current_image_data)
                            else:
                                logger.warning(f"Objeto de imagen no procesado en página {page_idx}: tipo {type(img_obj)}")
                    
                    processed_pages_data.append(page_data)
                
                concatenated_markdown = "\n\n".join(all_page_markdowns)

                if not concatenated_markdown.strip() and not any(p.get("images") for p in processed_pages_data if include_images):
                    logger.warning(
                        f"OCR para '{pdf_file.filename}' produjo páginas pero "
                        "sin contenido de texto markdown extraíble ni imágenes (cuando solicitado)."
                    )
                logger.info(
                    f"Datos extraídos para '{pdf_file.filename}'. "
                    f"Número de páginas procesadas: {len(processed_pages_data)}. "
                    f"Longitud total del markdown: {len(concatenated_markdown)} caracteres."
                )
            else:
                logger.warning(
                    f"OCR para '{pdf_file.filename}' no devolvió páginas o "
                    "contenido en la estructura esperada."
                )

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "fileName": pdf_file.filename,
                "pages": processed_pages_data,
                "concatenated_text": concatenated_markdown,
            }
        )

    except PiscoConfigurationError as e:
        error_msg = f"Error de configuración del cliente OCR: {str(e)}"
        logger.error(f"{error_msg} (Archivo: '{pdf_file.filename}')", exc_info=True)
        return JSONResponse(status_code=400, content={"success": False, "error": error_msg})
    except PiscoFileError as e:
        error_msg = "Error de archivo interno del servidor al procesar para OCR."
        logger.error(f"{error_msg} Detalles: {str(e)} (Archivo: '{pdf_file.filename}')", exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": error_msg})
    except PiscoNetworkError as e:
        error_msg = "Error de red o timeout al contactar el servicio OCR de Mistral."
        logger.error(f"{error_msg} Detalles: {str(e)} (Archivo: '{pdf_file.filename}')", exc_info=True)
        return JSONResponse(status_code=504, content={"success": False, "error": error_msg})
    except PiscoApiError as e:
        logger.error(
            f"Error API Mistral para '{pdf_file.filename}'. Status: {e.status_code}, "
            f"Details: {e.error_details}. Error: {e}", exc_info=True
        )
        status_code = e.status_code or 500
        detail_msg = f"Error de la API de Mistral ({status_code}): {e.error_details or str(e)}"
        if e.status_code == 401:
            detail_msg = "API Key de Mistral AI inválida o no autorizada."
        elif e.status_code in [400, 422]:
            detail_msg = f"Error de la API de Mistral (solicitud o archivo no procesable): {e.error_details or str(e)}"
        elif e.status_code and e.status_code >= 500:
            detail_msg = f"Error del servidor OCR de Mistral: {e.error_details or str(e)}"
        
        return JSONResponse(status_code=status_code, content={"success": False, "error": detail_msg})
    except HTTPException:
        # Re-lanzar HTTPExceptions que queremos que FastAPI maneje por defecto
        raise
    except Exception as e:
        logger.error(
            f"Error inesperado procesando '{pdf_file.filename}': {e}",
            exc_info=True, # Incluir traceback en el log para producción
        )
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Ocurrió un error inesperado en el servidor."}
        )
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
                logger.info(f"Archivo temporal '{tmp_file_path}' eliminado.")
            except OSError as e_unlink:
                logger.error(f"Error al eliminar el archivo temporal '{tmp_file_path}': {e_unlink}")
        
        if pdf_file and hasattr(pdf_file, 'close') and callable(pdf_file.close):
            try:
                # UploadFile.close() es asíncrono
                if hasattr(pdf_file, '_file') and not pdf_file._file.closed: # type: ignore
                     await pdf_file.close()
                     logger.debug(f"Closed UploadFile '{pdf_file.filename}'.") # logger.debug para no ser verboso en INFO
            except Exception as e_close:
                logger.warning(f"Error al intentar cerrar el archivo subido '{pdf_file.filename}': {e_close}")

# Este endpoint me da la hora
@app.get("/hora")
async def get_hora():
    return {"hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# --- Servir archivos estáticos del Frontend ---
CURRENT_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_DIR = os.path.dirname(CURRENT_SCRIPT_DIR)
FRONTEND_DIR = os.path.join(PROJECT_ROOT_DIR, "front")

if not os.path.isdir(FRONTEND_DIR):
    logger.error(
        f"El directorio del frontend '{FRONTEND_DIR}' no se encontró. "
        "Los archivos estáticos no se servirán. "
        "Asegúrate de que la estructura de carpetas es correcta: ./back/main.py y ./front/"
    )
else:
    try:
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
        logger.info(f"Archivos estáticos del frontend montados desde: {FRONTEND_DIR}")
        logger.info(f"Accede al frontend en la raíz (ej. http://localhost:8000/)")
    except RuntimeError as e_mount:
        logger.error(f"Error al montar archivos estáticos desde '{FRONTEND_DIR}': {e_mount}")

# Adaptador Mangum para AWS Lambda
handler = Mangum(app)

# Bloque para desarrollo local con Uvicorn
if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor Uvicorn para desarrollo local.")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info" # Nivel de log de Uvicorn para desarrollo (puede ser "debug" si necesitas más detalle de Uvicorn)
    )