# backend/tests/test_main.py

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import io

# Asegúrate de que main.py esté en el PYTHONPATH o ajusta la importación
from main import app # Asumiendo que ejecutas pytest desde el directorio que contiene main.py

# Importa las excepciones de la librería pisco-mistral-ocr que tu código maneja
from pisco_mistral_ocr import (
    PiscoApiError,
    PiscoConfigurationError,
    PiscoFileError,
    PiscoNetworkError,
    OcrResult as PiscoOcrResult, # Renombrar para evitar conflicto con variables
    OcrPage as PiscoOcrPage
)

client = TestClient(app)

# --- Constantes para Mocks ---
MOCK_API_KEY = "hXLowB4SAo832zDyd6ijE7C30pXWMf7d"
MOCK_FILENAME = "test.pdf"
MOCK_PDF_CONTENT = b"dummy pdf content, lorem ipsum dolor sit amet consectetur adipiscing elit"
MOCK_OCR_TEXT = "dummy ocr text, lorem ipsum dolor sit amet consectetur adipiscing elit"

# --- Tests del Desarrollador Backend ---

def test_be_1_endpoint_ocr_pdf_exists_and_accepts_post():
    """
    Test BE-1: Endpoint /api/ocr-pdf Existe y Acepta POST
    Verifica que la ruta /api/ocr-pdf esté configurada y acepte el método POST.
    También verifica que rechace otros métodos como GET.
    """
    response_post = client.post("/api/ocr-pdf")
    assert response_post.status_code != 404, "El endpoint /api/ocr-pdf no fue encontrado."
    assert response_post.status_code != 405, "El método POST no está permitido, pero debería estarlo."

    response_get = client.get("/api/ocr-pdf")
    assert response_get.status_code == 405, "El método GET debería ser rechazado con un 405."

@patch('main.PiscoMistralOcrClient')
def test_be_2_solicitud_valida_simulacion_exitosa_mistral_ai(MockPiscoClient):
    """
    Test BE-2: Solicitud Válida - Simulación Exitosa de Mistral AI
    Prueba el flujo completo con una llamada mockeada exitosa a PiscoMistralOcrClient.
    """
    mock_ocr_client_instance = AsyncMock()
    mock_page = MagicMock(spec=PiscoOcrPage)
    mock_page.markdown = MOCK_OCR_TEXT
    mock_ocr_result = MagicMock(spec=PiscoOcrResult)
    mock_ocr_result.pages = [mock_page]
    mock_ocr_client_instance.ocr = AsyncMock(return_value=mock_ocr_result)
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance

    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 200, f"Respuesta inesperada: {response.text}"
    expected_json = {"success": True, "fileName": MOCK_FILENAME, "text": MOCK_OCR_TEXT}
    assert response.json() == expected_json

    MockPiscoClient.assert_called_once_with(api_key=MOCK_API_KEY, timeout=300.0) # 300.0 es OCR_CLIENT_TIMEOUT
    mock_ocr_client_instance.ocr.assert_called_once()
    # El primer argumento de ocr es el path temporal, el segundo es delete_after_processing
    assert mock_ocr_client_instance.ocr.call_args[0][1] is True # delete_after_processing=True

def test_be_3_solicitud_invalida_falta_api_key():
    """
    Test BE-3: Solicitud Inválida - Falta `api_key`
    Verifica que el endpoint devuelva un error 422 con el formato esperado.
    """
    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}

    response = client.post("/api/ocr-pdf", files=files)

    assert response.status_code == 422, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data.get("success") is False
    assert response_data.get("error") == "El campo 'api_key' es requerido o inválido."

def test_be_4_solicitud_invalida_falta_pdf_file():
    """
    Test BE-4: Solicitud Inválida - Falta `pdf_file`
    Verifica que el endpoint devuelva un error 422 con el formato esperado.
    """
    data = {'api_key': MOCK_API_KEY}
    response = client.post("/api/ocr-pdf", data=data)

    assert response.status_code == 422, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data.get("success") is False
    assert response_data.get("error") == "El campo 'pdf_file' es requerido o inválido."

@patch('main.PiscoMistralOcrClient')
def test_be_5_simulacion_error_api_key_invalida_mistral_ai_401(MockPiscoClient):
    """
    Test BE-5: Simulación de Error de API Key Inválida en Mistral AI (401)
    Mockea PiscoMistralOcrClient para que devuelva PiscoApiError 401.
    """
    mock_ocr_client_instance = AsyncMock()
    mock_ocr_client_instance.ocr = AsyncMock(
        side_effect=PiscoApiError(
            status_code=401,
            error_details={"message": "Invalid API Key"} # El contenido de error_details puede variar
        )
    )
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance
    
    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': "invalid-mistral-key"}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 401, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['error'] == "API Key de Mistral AI inválida o no autorizada."

@patch('main.PiscoMistralOcrClient')
def test_be_6_simulacion_error_servidor_mistral_ai_500(MockPiscoClient):
    """
    Test BE-6: Simulación de Error de Servidor en Mistral AI (500 - PiscoApiError)
    Mockea PiscoMistralOcrClient para que devuelva PiscoApiError 500.
    ASUME que el backend se modificará para devolver {"success": False, "error": "..."}
    """
    error_details_mock = {"message": "Mistral Internal Server Error", "type": "internal_error"}
    pisco_api_error_instance = PiscoApiError(status_code=500, error_details=error_details_mock)

    mock_ocr_client_instance = AsyncMock()
    mock_ocr_client_instance.ocr = AsyncMock(side_effect=pisco_api_error_instance)
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance

    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 500, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data["success"] is False
    # El mensaje de error exacto dependerá de cómo lo construyas en main.py
    # basado en e.error_details or str(e)
    expected_error_message = f"Error del servidor OCR de Mistral: {error_details_mock}"
    assert response_data["error"] == expected_error_message


@patch('shutil.copyfileobj') # Mockear una operación interna propensa a fallos
def test_be_7_simulacion_error_inesperado_durante_procesamiento(mock_copyfileobj):
    """
    Test BE-7: Simulación de Error Inesperado Durante Procesamiento (Excepción Genérica)
    Fuerza una excepción genérica no controlada específicamente por los otros handlers.
    """
    mock_copyfileobj.side_effect = Exception("Fallo catastrófico simulado en copyfileobj")
    
    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 500, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data["success"] is False
    assert response_data["error"] == "Ocurrió un error inesperado en el servidor."

@patch('main.PiscoMistralOcrClient')
def test_be_8_manejo_archivo_pdf_vacio_o_corrupto_simulado_400(MockPiscoClient):
    """
    Test BE-8: Manejo de Archivo PDF Inválido (Error 400 desde Mistral - PiscoApiError)
    Mockea PiscoMistralOcrClient para que devuelva PiscoApiError 400.
    """
    error_details_mock = {"message": "Invalid PDF file format", "type": "invalid_request_error"}
    pisco_api_error_instance = PiscoApiError(status_code=400, error_details=error_details_mock)

    mock_ocr_client_instance = AsyncMock()
    mock_ocr_client_instance.ocr = AsyncMock(side_effect=pisco_api_error_instance)
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance
    
    # Archivo "malo" o vacío
    bad_pdf_content = b"this is not a pdf"
    pdf_file_in_memory = io.BytesIO(bad_pdf_content)
    files = {'pdf_file': ('bad.pdf', pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)
    
    assert response.status_code == 400, f"Respuesta inesperada: {response.text}"
    response_data = response.json()
    assert response_data["success"] is False
    expected_error_message = f"Error de la API de Mistral (solicitud o archivo no procesable): {error_details_mock}"
    assert response_data["error"] == expected_error_message

# --- Nuevos Tests para Cobertura Adicional de Errores (ASUMEN Backend Modificado) ---

@patch('main.PiscoMistralOcrClient')
def test_be_9_pisco_network_error_simulado(MockPiscoClient):
    """
    Test BE-9: Simulación de PiscoNetworkError
    ASUME que el backend se modificará para devolver {"success": False, "error": "..."}
    """
    error_message = "Simulated network failure."
    pisco_network_error_instance = PiscoNetworkError(error_message)

    mock_ocr_client_instance = AsyncMock()
    mock_ocr_client_instance.ocr = AsyncMock(side_effect=pisco_network_error_instance)
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance

    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 504, f"Respuesta inesperada: {response.text}" # Código de PiscoNetworkError en main.py
    response_data = response.json()
    assert response_data["success"] is False
    # El mensaje exacto que tu backend construirá para este error
    assert response_data["error"] == "Error de red o timeout al contactar el servicio OCR de Mistral."


@patch('main.PiscoMistralOcrClient') # O mockear la operación de guardado de archivo si PiscoFileError es por eso
def test_be_10_pisco_file_error_simulado(MockPiscoClient):
    """
    Test BE-10: Simulación de PiscoFileError (durante la interacción con Pisco)
    ASUME que el backend se modificará para devolver {"success": False, "error": "..."}
    """
    # PiscoFileError se usa en la librería cliente para problemas con el archivo local ANTES de la llamada.
    # En tu main.py, el PiscoFileError que manejas viene del cliente pisco-mistral-ocr
    # Si el cliente pisco-mistral-ocr intentara leer un archivo que ya no existe tras ser guardado
    # temporalmente (difícil de simular aquí sin conocer internals de Pisco), o si Pisco
    # levantara FileError por otra razón.
    # Para este test, simularemos que client.ocr() lo levanta.
    error_message = "Simulated file error from Pisco client."
    pisco_file_error_instance = PiscoFileError(error_message)

    mock_ocr_client_instance = AsyncMock()
    mock_ocr_client_instance.ocr = AsyncMock(side_effect=pisco_file_error_instance)
    MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance

    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY}

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 500, f"Respuesta inesperada: {response.text}" # Código de PiscoFileError en main.py
    response_data = response.json()
    assert response_data["success"] is False
    assert response_data["error"] == "Error de archivo interno del servidor al procesar para OCR."


@patch('main.PiscoMistralOcrClient')
def test_be_11_pisco_configuration_error_simulado(MockPiscoClient):
    """
    Test BE-11: Simulación de PiscoConfigurationError al instanciar el cliente.
    ASUME que el backend se modificará para devolver {"success": False, "error": "..."}
    """
    # Esta prueba es un poco más compleja de configurar porque el error ocurre
    # al instanciar PiscoMistralOcrClient, no al llamar a un método.
    # La validación de FastAPI para api_key (min_length=1) ya cubre el caso de clave vacía.
    # PiscoConfigurationError se lanzaría si api_key es None o hay otro problema de config.
    # Aquí simulamos que la instanciación falla.
    error_message = "Simulated configuration error."
    pisco_config_error_instance = PiscoConfigurationError(error_message)
    MockPiscoClient.side_effect = pisco_config_error_instance # Error en la instanciación

    pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
    files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'application/pdf')}
    data = {'api_key': MOCK_API_KEY} # API key está presente para pasar validación FastAPI

    response = client.post("/api/ocr-pdf", files=files, data=data)

    assert response.status_code == 400, f"Respuesta inesperada: {response.text}" # Código de PiscoConfigError en main.py
    response_data = response.json()
    assert response_data["success"] is False
    # El mensaje exacto que tu backend construirá
    assert response_data["error"] == f"Error de configuración del cliente OCR: {error_message}"

def test_content_type_not_pdf_warning_but_proceeds(caplog):
    """
    Test que verifica que se loguea un warning si el content_type no es PDF,
    pero el proceso continúa (simulando un éxito del OCR).
    """
    with patch('main.PiscoMistralOcrClient') as MockPiscoClient:
        mock_ocr_client_instance = AsyncMock()
        mock_page = MagicMock(spec=PiscoOcrPage)
        mock_page.markdown = MOCK_OCR_TEXT
        mock_ocr_result = MagicMock(spec=PiscoOcrResult)
        mock_ocr_result.pages = [mock_page]
        mock_ocr_client_instance.ocr = AsyncMock(return_value=mock_ocr_result)
        MockPiscoClient.return_value.__aenter__.return_value = mock_ocr_client_instance

        pdf_file_in_memory = io.BytesIO(MOCK_PDF_CONTENT)
        # Content-Type incorrecto
        files = {'pdf_file': (MOCK_FILENAME, pdf_file_in_memory, 'image/png')}
        data = {'api_key': MOCK_API_KEY}

        import logging
        caplog.set_level(logging.WARNING, logger="main") # Captura warnings del logger de main.py

        response = client.post("/api/ocr-pdf", files=files, data=data)

        assert response.status_code == 200
        assert response.json()["success"] is True
        
        assert any(
            f"Archivo '{MOCK_FILENAME}' subido con tipo de contenido inesperado: 'image/png'" in record.message
            for record in caplog.records
        )
        assert any(
            "Se intentará procesar." in record.message
            for record in caplog.records
        )