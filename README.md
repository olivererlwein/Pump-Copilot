# Pump Copilot — Phone MVP

PWA privada para iPhone + backend FastAPI.

## Incluye
- Las 5 wallets iniciales.
- Monitor de operaciones mediante PumpPortal.
- SQLite para guardar actividad.
- Señal cuando 2 o más traders compran el mismo mint dentro de 90 segundos.
- Dashboard móvil instalable en la pantalla de inicio.
- Token privado de acceso.
- Solo observación/paper workflow: esta versión NO ejecuta compras.

## Ejecutar
1. `python -m venv .venv`
2. Activar el entorno.
3. `pip install -r requirements.txt`
4. Copiar `.env.example` a `.env`
5. Poner `PUMPPORTAL_API_KEY` y cambiar `APP_TOKEN`
6. `uvicorn app:app --host 0.0.0.0 --port 8000`

Para usarla en iPhone fuera de tu red local, desplegá esta carpeta en un servidor HTTPS. Luego Safari > Compartir > Añadir a pantalla de inicio.

## Seguridad
Nunca pongas seed phrase ni private key en este MVP. La primera fase es monitor + paper trading.
