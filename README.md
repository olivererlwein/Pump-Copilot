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

## Piloto Solana con RPC alternativo

El piloto `HELIUS_STANDARD_WSS_*` habla el protocolo estándar de Solana y
también acepta un proveedor distinto de Helius. Si se configura una URL WSS
explícita, se exige su propia URL HTTPS para `getTransaction`: el RPC global
`SOLANA_RPC_URL` no cambia. No guardes estas URL (contienen la API key) en Git;
configuralas como variables privadas en Railway.

Para Alchemy Solana mainnet:

- `HELIUS_STANDARD_WSS_URL`: URL WSS de la app de Alchemy.
- `HELIUS_STANDARD_WSS_RPC_URL`: URL HTTPS RPC de la misma app.
- `HELIUS_STANDARD_WSS_TRADERS`: nombres de 2 o 3 traders de `WATCHED_WALLETS`, separados por comas. Si se omite, se suscriben todos.
- `HELIUS_STANDARD_WSS_ENABLED=true`: iniciar el piloto solo cuando las URL y el filtro esten listos.
- Mantener `HELIUS_STANDARD_WSS_APPLY=false` y `HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED=false` durante la medicion inicial.

Revisar `/api/helius-standard-wss-stats`: `configured`, `selected_wallets`,
`runtime.connected`, `last_24h.pump_log_notifications`, `parsed_events`,
`rpc_fetch_attempts` y `runtime.last_error`. En proveedor `custom`, la
proyeccion de creditos de Helius no aplica: comprobar uso y limites en el
panel del proveedor. Si hay errores persistentes, cero eventos relevantes o
consumo excesivo, apagar `HELIUS_STANDARD_WSS_ENABLED` y revisar antes de
ampliar la prueba. Esta configuracion no habilita trading real.
