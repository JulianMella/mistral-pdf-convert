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
"""

import logging
import os
import shutil
import tempfile
from typing import Dict, Any, List

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
)

# NUEVA IMPORTACIÓN para servir archivos estáticos
from fastapi.staticfiles import StaticFiles
# FileResponse podría ser útil para servir archivos individualmente si no se usa StaticFiles con html=True
# from fastapi.responses import FileResponse, HTMLResponse 

# Configuración del logger para este módulo.
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# --- Constantes y Configuración Global ---

# Lista de orígenes permitidos para CORS.
# En producción, esta lista DEBE restringirse a los dominios específicos
# del frontend desplegado para mayor seguridad.
# Si FastAPI sirve el frontend, las peticiones del JS al API serán del mismo origen.
ALLOWED_ORIGINS: List[str] = [
    "http://localhost",
    "http://localhost:8000", # Puerto por defecto de Uvicorn
    "http://localhost:8080",
    "http://127.0.0.1:5500", # Puede que ya no sea necesario si FastAPI sirve el frontend
    # "https://tu-app.netlify.app", # DESCOMENTAR Y AJUSTAR PARA PRODUCCIÓN
]

# Timeout para las operaciones del cliente OCR en segundos.
OCR_CLIENT_TIMEOUT: float = 300.0  # 5 minutos

# --- Aplicación FastAPI ---

app = FastAPI(
    title="OCR PDF Service with Frontend",
    description=(
        "API para extraer texto de archivos PDF usando Mistral AI "
        "a través de PiscoMistralOcrClient, y servir una interfaz de usuario."
    ),
    version="1.0.0",
)

# Configuración del Middleware CORS.
# Permite que el frontend (servido desde un origen diferente, o el mismo) interactúe
# de forma segura con esta API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"], # Añadido GET para el frontend
    allow_headers=["Content-Type"],
)


# --- Manejadores de Excepciones Personalizados ---

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Manejador personalizado para los errores de validación de FastAPI (422).

    Formatea la respuesta para que coincida con la estructura esperada por
    los tests del frontend: `{"success": false, "error": "mensaje..."}`.
    """
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
        "Recibe una API Key de Mistral AI y un archivo PDF, realiza OCR "
        "y devuelve el texto extraído."
    ),
    response_model=None, # Se define la respuesta explícitamente, incluyendo errores
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
) -> JSONResponse: # Cambiado para reflejar que siempre devolvemos JSONResponse
    """
    Procesa un archivo PDF subido para extraer su texto usando Mistral AI.
    """
    if not pdf_file.filename:
        logger.warning("Intento de subida de archivo PDF sin nombre.")
        raise HTTPException(
            status_code=400,
            detail="El archivo PDF debe tener un nombre." 
        )


    if pdf_file.content_type != "application/pdf":
        logger.warning(
            f"Archivo '{pdf_file.filename}' subido con tipo de contenido "
            f"inesperado: '{pdf_file.content_type}'. Se intentará procesar."
        )

    tmp_file_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".pdf"
        ) as tmp_file_obj:
            shutil.copyfileobj(pdf_file.file, tmp_file_obj)
            tmp_file_path = tmp_file_obj.name

        api_key_suffix = api_key[-4:] if len(api_key) >= 4 else "..."
        logger.info(
            f"Archivo PDF '{pdf_file.filename}' guardado temporalmente en "
            f"'{tmp_file_path}'. API Key (parcial): ...{api_key_suffix}"
        )

        extracted_text = ""
        async with PiscoMistralOcrClient(
            api_key=api_key, timeout=OCR_CLIENT_TIMEOUT
        ) as client:
            logger.info(
                f"Iniciando OCR para '{tmp_file_path}' con PiscoMistralOcrClient "
                f"(timeout: {OCR_CLIENT_TIMEOUT}s)."
            )
            ocr_result = await client.ocr(
                tmp_file_path, delete_after_processing=True
            )
            logger.info(
                f"OCR completado para '{tmp_file_path}'. Archivo en Mistral "
                "eliminado (solicitado por delete_after_processing)."
            )

            if ocr_result and ocr_result.pages:
                page_texts = [
                    page.markdown
                    for page in ocr_result.pages
                    if page.markdown
                ]
                extracted_text = "\n\n".join(page_texts)
                if not extracted_text.strip():
                    logger.warning(
                        f"OCR para '{pdf_file.filename}' produjo páginas pero "
                        "sin contenido de texto markdown extraíble."
                    )
                logger.info(
                    f"Texto extraído para '{pdf_file.filename}'. "
                    f"Longitud: {len(extracted_text)} caracteres."
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
                "text": extracted_text,
            }
        )

    except PiscoConfigurationError as e:
        error_msg = f"Error de configuración del cliente OCR: {str(e)}"
        logger.error(f"{error_msg} (Archivo: '{pdf_file.filename}')")
        return JSONResponse(
            status_code=400, # Error de cliente por configuración incorrecta
            content={"success": False, "error": error_msg},
        )
    except PiscoFileError as e:
        error_msg = "Error de archivo interno del servidor al procesar para OCR."
        logger.error(f"{error_msg} Detalles: {str(e)} (Archivo: '{pdf_file.filename}')")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": error_msg},
        )
    except PiscoNetworkError as e:
        error_msg = "Error de red o timeout al contactar el servicio OCR de Mistral."
        logger.error(f"{error_msg} Detalles: {str(e)} (Archivo: '{pdf_file.filename}')")
        return JSONResponse(
            status_code=504, # Gateway Timeout
            content={"success": False, "error": error_msg},
        )
    except PiscoApiError as e:
        logger.error(
            f"Error API Mistral para '{pdf_file.filename}'. Status: {e.status_code}, "
            f"Details: {e.error_details}. Error: {e}"
        )
        status_code = e.status_code or 500 # Default a 500 si no hay status_code
        
        if e.status_code == 401:
            detail_msg = "API Key de Mistral AI inválida o no autorizada."
        elif e.status_code in [400, 422]: # Errores de solicitud a Mistral
            detail_msg = (
                f"Error de la API de Mistral (solicitud o archivo no "
                f"procesable): {e.error_details or str(e)}"
            )
        elif e.status_code and e.status_code >= 500: # Errores del servidor Mistral
            detail_msg = (
                f"Error del servidor OCR de Mistral: {e.error_details or str(e)}"
            )
        else: # Otros errores de la API de Mistral
            detail_msg = (
                f"Error de la API de Mistral ({status_code}): "
                f"{e.error_details or str(e)}"
            )
        
        return JSONResponse(
            status_code=status_code,
            content={"success": False, "error": detail_msg}
        )

    except HTTPException:
        # Re-lanzar HTTPExceptions que queremos que FastAPI maneje por defecto
        # (como la de nombre de archivo faltante).
        raise
    except Exception as e:
        logger.error(
            f"Error inesperado procesando '{pdf_file.filename}': {e}",
            exc_info=True, # Incluir traceback en el log
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "Ocurrió un error inesperado en el servidor."
            }
        )
    finally:
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
                logger.info(f"Archivo temporal '{tmp_file_path}' eliminado.")
            except OSError as e_unlink:
                logger.error(
                    f"Error al eliminar el archivo temporal "
                    f"'{tmp_file_path}': {e_unlink}"
                )
        if hasattr(pdf_file, 'file') and hasattr(pdf_file.file, 'close'):
            try:
                if not pdf_file.file.closed: # type: ignore
                    pdf_file.file.close()
            except Exception as e_close:
                logger.warning(
                    f"Error al intentar cerrar el archivo subido "
                    f"'{pdf_file.filename}': {e_close}"
                )

# --- Servir archivos estáticos del Frontend ---
# Determinar la ruta al directorio 'front'
# Asumiendo que este archivo (main.py) está en 'mistral-pdf-convert/back/main.py'
CURRENT_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# PROJECT_ROOT_DIR será 'mistral-pdf-convert'
PROJECT_ROOT_DIR = os.path.dirname(CURRENT_SCRIPT_DIR)
# FRONTEND_DIR será 'mistral-pdf-convert/front'
FRONTEND_DIR = os.path.join(PROJECT_ROOT_DIR, "front")

# Comprobar si el directorio del frontend existe
if not os.path.isdir(FRONTEND_DIR):
    logger.error(
        f"El directorio del frontend '{FRONTEND_DIR}' no se encontró. "
        "Los archivos estáticos no se servirán. "
        "Asegúrate de que la estructura de carpetas es correcta: ./back/main.py y ./front/"
    )
else:
    # Montar la carpeta 'front' para servir archivos estáticos.
    # html=True permite que sirva 'index.html' para el path '/' por defecto.
    # También servirá style.css en /style.css y script.js en /script.js
    # si index.html los referencia de forma relativa (ej: <link href="style.css">).
    # Esta línea debe ir después de la definición de las rutas API para evitar conflictos,
    # aunque con el prefijo /api/ para las rutas API, no debería haber problema.
    try:
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
        logger.info(f"Archivos estáticos del frontend montados desde: {FRONTEND_DIR}")
        logger.info(f"Accede al frontend en la raíz (ej. http://localhost:8000/)")
    except RuntimeError as e_mount:
        logger.error(f"Error al montar archivos estáticos desde '{FRONTEND_DIR}': {e_mount}")


# Adaptador Mangum para AWS Lambda (Netlify Functions).
# Esto debe ir después de toda la configuración de la app.
handler = Mangum(app)

# Bloque para desarrollo local con Uvicorn.
if __name__ == "__main__":
    import uvicorn
    logger.info("Iniciando servidor Uvicorn para desarrollo local.")
    # Asegúrate de que Uvicorn recarga los cambios si es necesario durante el desarrollo.
    # El host 0.0.0.0 permite el acceso desde otras máquinas en la red local.
    uvicorn.run(
        "main:app", # 'main' es el nombre de este archivo (main.py), 'app' es el objeto FastAPI
        host="0.0.0.0",
        port=8000,
        reload=True # Habilita la recarga automática en desarrollo
    )
