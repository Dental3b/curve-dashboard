# Mensaje diario de Dental 3B

Este cambio recupera el programa Python reemplazado accidentalmente por YAML y prepara el envío por Meta Cloud API desde el número ya usado por PDR. No cambia el registro del número, las llamadas ni los webhooks de PDR.

## Configuración pendiente antes de activar

El propietario comunicó que la plantilla ya está aprobada el 23 de octubre de 2026 (America/New_York). Falta verificar el código de idioma exacto y completar las variables de GitHub antes del primer envío. El propietario retomó la prueba y pidió recibirla en el número API de PDR. Ese destino está bloqueado: Meta documenta como no compatibles los mensajes enviados mediante API a otro número ya registrado en la API. Un segundo emisor API no resuelve esta limitación. Pendiente de acordar un destinatario compatible; no registrar ni migrar números para intentar resolverlo y no activar envíos.

Plantilla creada en Meta WhatsApp Manager, dentro de la misma cuenta que contiene el número registrado de PDR:

- Nombre: `dental3b_resumen_diario`
- Idioma: español (`es`; si Meta devuelve otro código, usar exactamente ese código en GitHub).
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
| Variable | `WHATSAPP_TEMPLATE_LANGUAGE` | `es`, o el código exacto aprobado |
| Variable | `WHATSAPP_GRAPH_VERSION` | `v23.0`, versión del envío PDR documentado |
| Variable | `DAILY_SUMMARY_ENABLED` | Dejar sin definir hasta terminar la verificación; después `true` |

La configuración PDR está en su Secret Manager de Google Cloud. El secret `WHATSAPP_TOKEN` de GitHub puede contener el token solo o la configuración JSON / `WHATSAPP_TOKEN=valor` de Google Cloud: el programa extrae exclusivamente el campo de token y elimina saltos de línea de copiado. No modifica el secreto PDR ni publica credenciales. Los identificadores de cuenta y teléfonos se configuran en las variables de GitHub, sin valores predeterminados en el programa de envío.

Verificación real del 24 de octubre de 2026 (UTC): [ejecución 40054517313](https://github.com/Dental3b/curve-dashboard/actions/runs/40054517313) confirmó que GitHub tiene el secreto, Meta lo acepta para el número esperado y Curve permite recuperar ledger y citas. Los logs solo muestran éxito o errores sanitizados, sin totales financieros ni pacientes. Esto verifica acceso actual; no garantiza vigencia indefinida del token ni entrega de mensajes. La aprobación fue comunicada posteriormente por el propietario; el código de idioma sigue pendiente de verificar. No se envió WhatsApp.

## Verificación y activación

Antes de activar, resolver el destinatario conforme a la [referencia oficial de mensajes no compatibles de Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/unsupported). El número API de PDR conserva su registro y automatizaciones. La comprobación de GitHub [40057058534](https://github.com/Dental3b/curve-dashboard/actions/runs/40057058534) detectó que faltan `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TO`, `WHATSAPP_TEMPLATE_NAME` y `WHATSAPP_TEMPLATE_LANGUAGE`. Ninguna prueba ha enviado mensajes.

1. Integrar este cambio y ejecutar **Actions → Daily Summary → Run workflow** con `send` desmarcado y `report_date` en `2026-10-23`. La vista previa solo consulta Curve; no escribe estado ni envía WhatsApp. Comparar los totales con Curve.
2. Una vez aprobada la plantilla y configurado el token, establecer `DAILY_SUMMARY_ENABLED=true`.
3. Dentro de la ventana de envío, el primer control que encuentre el cierre guarda la hora. El siguiente control, al menos 15 minutos después y con los mismos totales, envía. También se puede ejecutar manualmente con `send` marcado, respetando los mismos controles; no hay un botón que ignore la protección contra duplicados.
4. Verificar recepción en el WhatsApp del destinatario. Una respuesta con `wamid` confirma aceptación de Meta; la entrega final se comprueba en el dispositivo o en el webhook de estado de WhatsApp ya existente. Este cambio no modifica ese webhook.

## Horario y reglas

- Zona horaria: `America/New_York`, incluido cambio de horario de verano.
- Conserva las ventanas del último workflow funcional: lunes a viernes desde las 16:00 hasta antes de las 21:00; sábados desde las 14:00 hasta antes de las 19:00; sin envío domingo. El programador comprueba cada 15 minutos; GitHub puede retrasar u omitir ejecuciones.
- Envía cuando todas las citas consultadas están terminadas, canceladas o ausentes y los totales llevan al menos 15 minutos estables. No se deduce que la clínica cerró de una hora de cita sin verificar su estado. Si quedan citas activas, no envía y habrá que corregirlas en Curve.
- Si hay producción pero no citas, espera hasta las 20:00 entre semana o 18:00 sábado, más el intervalo de estabilidad. Sin producción positiva, no envía, igual que el programa anterior.
- Si el último control llega tarde o los totales siguen cambiando al final de la ventana, puede no salir ese día. Revisar la ejecución; no se envía retrospectivamente de forma automática.
- Producción y cobros se suman desde `calculations.production` y `calculations.payments`, con decimales y todas las páginas. El rango de ledger termina al comienzo del día siguiente, exclusivo.
- El antiguo “Patients seen” cuenta pacientes únicos con producción positiva; no cuenta citas en Check Out. Se conserva el cálculo y la plantilla lo nombra “Pacientes con producción”.

## Estado y fallos

Los runners de GitHub son temporales: un archivo en `/tmp` no evita duplicados entre ejecuciones. El nuevo programa guarda una marca por fecha en la rama `daily-summary-state`, con permisos `contents: write` del `GITHUB_TOKEN` de Actions. No necesita otro token de GitHub. Esta rama conserva únicamente fecha, estado, huella de totales y un identificador de mensaje; no guarda pacientes ni credenciales. La rama se crea en el primer envío habilitado.

El workflow serializa las ejecuciones y cada escritura verifica el SHA actual. Un fallo al reservar el envío detiene el POST. `accepted` impide repetir la fecha, aunque se vuelva a ejecutar el workflow. `sending` significa que el resultado es incierto o que se interrumpió el programa entre la reserva y la confirmación: no se reintenta automáticamente, para evitar mensajes duplicados. Una respuesta HTTP 4xx explícita (salvo 408) permite volver a intentar tras corregir la causa. Un fallo de red o 5xx conserva el estado incierto.

Para resolver un estado `sending`, comprobar primero el mensaje en Meta o en el destinatario. Si llegó, cambiar el estado a `accepted` conservando el ID conocido. Solo si se confirma que no se envió, cambiarlo a `waiting` en el archivo de esa fecha; el siguiente control vuelve a esperar 15 minutos. No borrar marcas de mensajes aceptados.

No se garantiza entrega exactamente una vez: una respuesta perdida después del POST requiere revisión. El programa prioriza no repetir envíos dudosos y muestra fallo en Actions. No registra un mensaje rechazado como exitoso.

## Pruebas

```bash
python -m unittest discover -s tests -v
```

Las 21 pruebas unitarias usan respuestas simuladas: ledger de doble entrada, paginación, fallos parciales, horario de verano, cierre estable, rechazo de Meta, resultados inciertos, escrituras de estado en conflicto y extracción del token desde la configuración de Google Cloud. No usan credenciales ni envían mensajes. El workflow `Verify Curve and Meta connections` realiza por separado consultas de lectura con los secretos guardados; nunca envía WhatsApp. Solo se usa la biblioteca estándar de Python 3.11+.

Referencias: [envío de plantillas de Meta](https://www.postman.com/meta/whatsapp-business-platform/request/o65u5m5/send-message-template-text), [Messages de Meta](https://www.postman.com/meta/whatsapp-business-platform/folder/o48mro7/messages), [GitHub Contents API](https://docs.github.com/en/rest/repos/contents), [programación de Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
