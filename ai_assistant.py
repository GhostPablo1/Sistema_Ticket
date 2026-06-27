"""
Módulo de asistente IA para soporte técnico.
Usa OpenRouter (compatible con OpenAI SDK) con fallback basado en reglas.
Soporta historial de conversación para mantener contexto entre mensajes.
"""
import os
from typing import List, Dict

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

_SYSTEM_PROMPT = """Eres el asistente virtual de soporte técnico de la Municipalidad Distrital de Bellavista.
Tu función es ayudar a los trabajadores a resolver su problema técnico ESPECÍFICO mientras el técnico asignado llega a su área.

Reglas:
- Responde SIEMPRE en español.
- Analiza exactamente lo que describe el usuario y da pasos específicos para ESE problema concreto.
- NO des pasos genéricos que no apliquen al problema descrito.
- Si el usuario dice "tinta baja", habla de tinta. Si dice "papel atascado", habla de atasco. Si dice "pantalla azul", habla de pantalla azul.
- Usa pasos numerados, claros y accionables.
- Si el problema requiere que el técnico intervenga físicamente, dilo con amabilidad y recuérdale al usuario que el técnico ya está en camino.
- Máximo 6 pasos por respuesta. Sé preciso, no repitas información obvia.
- Recuerda el contexto de la conversación: si el usuario ya intentó algo, no vuelvas a sugerirlo."""


def _fallback_response(user_question: str, user_name: str) -> str:
    """Respuesta basada en reglas cuando la API no está disponible."""
    q = user_question.lower()
    if any(w in q for w in ["tinta", "tóner", "toner", "cartucho", "borroso", "manchas"]):
        return (
            f"Hola {user_name}, para el problema de tinta/tóner:\n\n"
            "1. Verifica el nivel de tinta desde HP Smart o el panel de la impresora.\n"
            "2. Si el cartucho está bajo, retíralo y agítalo suavemente de lado a lado para redistribuir la tinta.\n"
            "3. Ve a Configuración de la impresora → Mantenimiento → Limpieza de cabezales.\n"
            "4. Imprime una página de prueba para verificar la mejora.\n"
            "5. Si el problema persiste, el técnico traerá un cartucho de repuesto."
        )
    if any(w in q for w in ["atasco", "papel", "atascado", "trabado"]):
        return (
            f"Hola {user_name}, para el atasco de papel:\n\n"
            "1. Apaga la impresora inmediatamente.\n"
            "2. Abre todas las tapas accesibles (frontal, posterior y bandeja).\n"
            "3. Jala el papel atascado con ambas manos de forma suave y pareja, sin rasgarlo.\n"
            "4. Revisa que no queden trozos de papel dentro.\n"
            "5. Cierra las tapas, enciende la impresora y haz una prueba de impresión.\n"
            "6. Si el atasco persiste o hay trozos dentro, espera al técnico."
        )
    if any(w in q for w in ["impresora", "imprimir", "imprime", "impresión"]):
        return (
            f"Hola {user_name}, para tu problema con la impresora:\n\n"
            "1. Verifica que esté encendida y el cable USB/red conectado.\n"
            "2. Asegúrate de que sea la impresora predeterminada en Windows.\n"
            "3. Reiníciala: apágala 30 segundos y vuélvela a encender.\n"
            "4. Cancela los trabajos de impresión pendientes en la cola.\n"
            "5. Reinstala el driver desde el sitio del fabricante si el problema sigue."
        )
    if any(w in q for w in ["internet", "red", "wifi", "conexión", "conectar", "no carga"]):
        return (
            f"Hola {user_name}, para tu problema de conectividad:\n\n"
            "1. Verifica que el Wi-Fi o cable Ethernet esté activado.\n"
            "2. Reinicia el router: desconéctalo 30 segundos y vuélvelo a enchufar.\n"
            "3. Olvida la red Wi-Fi y vuelve a conectarte con la contraseña.\n"
            "4. Abre CMD como administrador: escribe 'ipconfig /release' + Enter, luego 'ipconfig /renew' + Enter.\n"
            "5. Si sigue sin funcionar, el técnico revisará la configuración de red."
        )
    if any(w in q for w in ["computadora", "pc", "pantalla", "lento", "cuelga", "congela", "reinicia", "apaga"]):
        return (
            f"Hola {user_name}, para tu problema con el equipo:\n\n"
            "1. Reinicia el equipo (resuelve la mayoría de problemas temporales).\n"
            "2. Si está lento: cierra programas innecesarios y libera espacio con 'Liberador de espacio en disco'.\n"
            "3. Si hay mensajes de error: anota o fotografía el mensaje exacto para el técnico.\n"
            "4. Si se apaga solo: asegúrate de que la ventilación no esté bloqueada.\n"
            "5. El técnico ya fue notificado y está en camino para ayudarte."
        )
    return (
        f"Hola {user_name}, describe con más detalle tu problema (qué equipo, qué pasa exactamente, "
        "qué mensaje de error ves si aplica). Mientras tanto, intenta reiniciar el equipo. "
        "El técnico ya fue notificado y está en camino."
    )


def get_ai_response(
    user_question: str,
    user_name: str,
    historial: List[Dict[str, str]] | None = None,
) -> str:
    """
    Genera una respuesta del asistente IA.

    Args:
        user_question: Pregunta o descripción del usuario.
        user_name: Nombre del usuario para personalizar la respuesta.
        historial: Lista de mensajes previos [{role, content}] para mantener contexto.

    Returns:
        Respuesta del asistente como string.
    """
    api_key  = os.getenv("ANTHROPIC_AUTH_TOKEN")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
    model    = os.getenv("ANTHROPIC_MODEL", "openai/gpt-oss-20b:free")

    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"

    if not model or model in ("openrouter/free",):
        model = "openai/gpt-oss-20b:free"

    if api_key:
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)

            system_content = (
                _SYSTEM_PROMPT
                + f"\n\nEl nombre del trabajador es: {user_name}. Dirígete a él por su nombre."
            )

            messages = [{"role": "system", "content": system_content}]

            # Incluir historial previo (máx 10 turnos para no saturar el contexto)
            if historial:
                messages.extend(historial[-20:])  # 10 turnos = 20 mensajes

            messages.append({"role": "user", "content": user_question})

            response = client.chat.completions.create(
                model=model,
                max_tokens=600,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[IA Error] {e}", flush=True)

    return _fallback_response(user_question, user_name)
