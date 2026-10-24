# Mensaje diario de Dental 3B

Este cambio recupera el programa Python reemplazado accidentalmente por YAML y prepara el envío por Meta Cloud API desde el número ya usado por PDR. No cambia el registro del número, las llamadas ni los webhooks de PDR.

## Estado de la configuración

La prueba del 23 de octubre de 2026 (America/New_York) fue aceptada por Meta y el propietario confirmó que recibió el mensaje en su WhatsApp personal. El emisor sigue siendo el número API existente de PDR. La variante aceptada es `es_MX`; queda fijada en los workflows para evitar otra edición manual. El intento anterior con `es` fue rechazado explícitamente con 132001 y no envió mensaje.

La [prueba real 40058797390](https://github.com/Dental3b/curve-dashboard/actions/runs/40058797390) terminó correctamente y el registro independiente está en `accepted`. El envío diario queda habilitado por defecto al integrar este cambio en `main`. La variable `DAILY_SUMMARY_ENABLED=false` permite pausarlo.

Plantilla creada en Meta WhatsApp Manager, dentro de la misma cuenta que contiene el número registrado de PDR:

- Nombre: `dental3b_resumen_diario`
- Idioma aprobado y comprobado mediante entrega: español de México (`es_MX`).
- Categoría solicitada: utilidad. Meta determina la categoría y aprobación final.
- Parámetros de cuerpo: cinco variables posicionales, en este orden: fecha, producción, cobros, porcentaje, pacientes con producción.

```text
🦷 Dental 3B - Resumen del día
📅 {{1}}

Producción: ${{2}}
Cobros: ${{3}} ({{4}}%)
{{5}} pacientes con producción.
```

Ejemplos para la revisión: `23 Oct 2026`, `3,381`, `1,798`, `53.2`, `9`.

En [GitHub → Settings → Secrets and variables → Actions](https://github.com/Dental3b/curve-dashboard/settings/secrets/actions):

| Tipo | Nombre | Valor |
| --- | --- | --- |
| Secret | `CURVE_CLIENT_ID` | Ya existente |
| Secret | `CURVE_CLIENT_SECRET` | Ya existente |
| Secret | `WHATSAPP_TOKEN` | Token de sistema Meta con acceso al número y permiso `whatsapp_business_messaging` |
| Variable | `WHATSAPP_PHONE_NUMBER_ID` | Identificador del número registrado de PDR, tomado de su configuración privada |
| Variable | `WHATSAPP_TO` | Número internacional del destinatario que ya usaba el resumen |
| Variable | `WHATSAPP_TEMPLATE_NAME` | `dental3b_resumen_diario`, cuando esté aprobada |
| Variable | `WHATSAPP_GRAPH_VERSION` | `v23.0`, versión del envío PDR documentado |
| Variable opcional | `DAILY_SUMMARY_ENABLED` | Habilitado por defecto (`true`); establecer `false` para pausar |

La configuración PDR está en su Secret Manager de Google Cloud. El secret `WHATSAPP_TOKEN` de GitHub puede contener el token solo o la configuración JSON / `WHATSAPP_TOKEN=valor` de Google Cloud: el programa extrae exclusivamente el campo de token y elimina saltos de línea de copiado. No modifica el secreto PDR ni publica credenciales. Los identificadores de cuenta y teléfonos se configuran en las variables de GitHub, sin valores predeterminados en el programa de envío.

Verificación real del 24 de octubre de 2026 (UTC): [ejecución 40054517313](https://github.com/Dental3b/curve-dashboard/actions/runs/40054517313) confirmó que GitHub tiene el secreto, Meta lo acepta para el número esperado y Curve permite recuperar ledger y citas. Los logs solo muestran éxito o errores sanitizados, sin totales financieros ni pacientes. Esto verifica acceso actual; no garantiza vigencia indefinida del token ni entrega de mensajes. Esta primera verificación solo consultó las APIs. La prueba de entrega posterior confirmó la variante `es_MX` y fue recibida por el propietario.

## Operación y comprobación

1. El workflow **Daily Summary** solo envía desde `main`. Está habilitado por defecto. Para pausarlo inmediatamente, establecer `DAILY_SUMMARY_ENABLED=false` y cancelar la ejecución activa en Actions: el proceso ya iniciado conserva las variables con las que arrancó. Reanudar con `true` permite los siguientes inicios.
2. La plantilla usa `es_MX` fijado en los workflows. La antigua variable `WHATSAPP_TEMPLATE_LANGUAGE` no se utiliza.
3. El primer control que detecta todas las citas del día cerradas inicia un plazo de 15 minutos. Al cumplirse, vuelve a consultar Curve y envía los totales actuales si las citas siguen cerradas. Un cambio de cobros no reinicia ese plazo; una cita pendiente o un cambio de citas sí lo reinicia.
4. La prueba manual usa `delivery_test.py`, el resumen de 2026-10-23 y la etiqueta `PRUEBA`. Su registro `delivery-test-v1.json` está separado de las fechas del envío diario. Volver a ejecutarla no repite el mensaje aceptado. El workflow de ejemplo solo permite ejecución manual; ya no se dispara al cambiar archivos.
5. Meta debe devolver un ID de mensaje para registrar aceptación. La recepción del ejemplo fue confirmada por el propietario; para futuros mensajes, el resultado de entrega se comprueba mediante el dispositivo o el webhook existente. No se modifica ese webhook.
6. La vista previa manual (`send` desmarcado) consulta Curve sin enviar ni escribir estado. Sus totales aparecen en los logs públicos del repositorio; no usarla para datos que no deban publicarse.

## Horario y reglas

- Zona horaria: `America/New_York`, incluido cambio de horario de verano.
- Revisa cada 5 minutos dentro de un monitor activo de hasta 5 horas y media, los siete días, sin esperar a una hora fija. Esto permite avisar si terminan temprano y también si atienden un domingo. El cron `2-59/5 * * * *` solicita ejecuciones sucesoras; la exclusión de concurrencia mantiene un solo monitor activo. Los cambios al programa o workflow en `main` también solicitan un inicio.
- Corrección del 24 de octubre: GitHub ejecutó el cron con separaciones de varias horas. La ejecución de las 14:49 detectó el cierre y quedó esperando otra ejecución; el reintento de las 15:09 fue aceptado por Meta. Ahora `monitor_daily_summary.py` hace las revisiones y completa los 15 minutos dentro de la misma ejecución, sin depender de otro evento cron. Cada consulta vuelve a usar la protección de envíos de `daily_summary.py`; el monitor no envía a Meta directamente ni borra estados.
- Envía cuando hay al menos una cita y todas las citas del día están en Check Out, Missed o Cancel (incluidas las variantes Cancelled/Canceled), después de 15 minutos desde la primera detección. Una cita activa, en Check In, con estado desconocido, Left Message o Cut impide el cierre. El personal debe registrar el estado real en Curve.
- Si una cita vuelve a estar pendiente durante la espera, se cancela el plazo y empieza de nuevo cuando todas estén cerradas. También reinicia si cambia el conjunto de citas, sus horarios o sus estados. Los cambios de producción o cobros no retrasan el aviso: se mandan los valores consultados al momento del envío.
- El mensaje incluye `Cierre detectado: 1:05 p. m. (hora de Georgia)` junto a la fecha, dentro de la primera variable de la plantilla aprobada. Es la hora en que la consulta encontró la agenda cerrada, no una medición de salida física ni una marca de asistencia del personal. No hace falta crear otra plantilla.
- También envía si todas las citas fueron canceladas o ausentes y la producción es cero. Si no hay ninguna cita, no manda un cierre ni inventa una hora de salida.
- El plazo es de al menos 15 minutos desde la detección, no desde la salida física. Mientras el monitor está activo y las APIs responden, normalmente sería unos 15–20 minutos desde el cambio en Curve. El monitor cubre retrasos del cron durante su ejecución, pero GitHub aún puede retrasar el arranque o el relevo, o interrumpir el runner; no es un servicio con garantía de disponibilidad. No se garantiza entrega exacta al minuto ni se detectan cambios transitorios entre consultas. Un cierre muy cercano a medianoche puede no enviarse; no hay recuperación automática de días anteriores.
- Cada monitor termina voluntariamente a los 330 minutos; el job permite 345 minutos, por debajo del límite de seis horas de GitHub. Una consulta que supera cuatro minutos se interrumpe; la marca durable `sending` sigue bloqueando reenvíos inciertos. Los errores se imprimen sanitizados y el monitor intenta las siguientes consultas sin saltarse las reglas de entrega. Al terminar, el job informa fallo si hubo consultas fallidas. Un cambio futuro de privacidad del repositorio puede afectar el costo de los minutos de Actions.
- Producción y cobros se suman desde `calculations.production` y `calculations.payments`, con decimales y todas las páginas. El rango de ledger termina al comienzo del día siguiente, exclusivo.
- El antiguo “Patients seen” cuenta pacientes únicos con producción positiva; no cuenta citas en Check Out. Se conserva el cálculo y la plantilla lo nombra “Pacientes con producción”.

## Estado y fallos

Los runners de GitHub son temporales: un archivo en `/tmp` no evita duplicados entre ejecuciones. El programa guarda una marca por fecha en la rama `daily-summary-state`, con permisos `contents: write` del `GITHUB_TOKEN` de Actions. No necesita otro token de GitHub. Esta rama conserva fecha, estado, hora de detección durante la espera y una huella irreversible de las citas; no guarda registros de pacientes ni credenciales. La rama se crea en el primer envío habilitado. Las marcas `ready` de la regla anterior reinician el plazo; las marcas `accepted` o `sending` mantienen la protección contra duplicados. Después de aceptar el reporte del día, las revisiones siguientes omiten las consultas a Curve y el envío.

El workflow serializa las ejecuciones y cada escritura verifica el SHA actual. Un fallo al reservar el envío detiene el POST. `accepted` impide repetir la fecha, aunque se vuelva a ejecutar el workflow. `sending` significa que el resultado es incierto o que se interrumpió el programa entre la reserva y la confirmación: no se reintenta automáticamente, para evitar mensajes duplicados. Una respuesta HTTP 4xx explícita (salvo 408) permite volver a intentar tras corregir la causa. Un fallo de red o 5xx conserva el estado incierto.

Para resolver un estado `sending`, comprobar primero el mensaje en Meta o en el destinatario. Si llegó, cambiar el estado a `accepted`. Solo si se confirma que no se envió, cambiarlo a `waiting` en el archivo de esa fecha; el siguiente control vuelve a esperar 15 minutos. No borrar marcas de mensajes aceptados.

No se garantiza entrega exactamente una vez: una respuesta perdida después del POST requiere revisión. El programa prioriza no repetir envíos dudosos y muestra fallo en Actions. No registra un mensaje rechazado como exitoso.

## Pruebas

```bash
python -m unittest discover -s tests -v
```

Las pruebas unitarias usan respuestas simuladas: ledger de doble entrada, paginación, fallos parciales, cierre temprano, fines de semana, espera de 15 minutos, citas reabiertas, cancelaciones sin producción, hora de detección con cambio de horario de verano, rechazo de Meta, resultados inciertos, escrituras de estado en conflicto, extracción del token y envío de un ejemplo independiente con protección contra duplicados. El monitor se prueba con reloj simulado: varias consultas sin otro cron, cadencia sin acumular tiempo de red, límites de duración y continuación después de errores sin reiniciar estados. No usan credenciales ni envían mensajes. El workflow `Verify Curve and Meta connections` realiza por separado consultas de lectura con los secretos guardados; nunca envía WhatsApp. Solo se usa la biblioteca estándar de Python 3.11+.

Referencias: [envío de plantillas de Meta](https://www.postman.com/meta/whatsapp-business-platform/request/o65u5m5/send-message-template-text), [Messages de Meta](https://www.postman.com/meta/whatsapp-business-platform/folder/o48mro7/messages), [GitHub Contents API](https://docs.github.com/en/rest/repos/contents), [programación de Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
