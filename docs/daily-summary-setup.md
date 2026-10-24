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

1. El workflow **Daily Summary** solo envía desde `main`. Está habilitado por defecto; puede pausarse con `DAILY_SUMMARY_ENABLED=false` y reanudarse con `true`.
2. La plantilla usa `es_MX` fijado en los workflows. La antigua variable `WHATSAPP_TEMPLATE_LANGUAGE` no se utiliza.
3. El primer control que detecta cierre guarda la hora. El siguiente, al menos 15 minutos después y con los mismos totales, envía.
4. La prueba manual usa `delivery_test.py`, el resumen de 2026-10-23 y la etiqueta `PRUEBA`. Su registro `delivery-test-v1.json` está separado de las fechas del envío diario. Volver a ejecutarla no repite el mensaje aceptado. El workflow de ejemplo solo permite ejecución manual; ya no se dispara al cambiar archivos.
5. Meta debe devolver un ID de mensaje para registrar aceptación. La recepción del ejemplo fue confirmada por el propietario; para futuros mensajes, el resultado de entrega se comprueba mediante el dispositivo o el webhook existente. No se modifica ese webhook.
6. La vista previa manual (`send` desmarcado) consulta Curve sin enviar ni escribir estado. Sus totales aparecen en los logs públicos del repositorio; no usarla para datos que no deban publicarse.

## Horario y reglas

- Zona horaria: `America/New_York`, incluido cambio de horario de verano.
- Conserva las ventanas del último workflow funcional: lunes a viernes desde las 16:00 hasta antes de las 21:00; sábados desde las 14:00 hasta antes de las 19:00; sin envío domingo. El programador comprueba cada 15 minutos; GitHub puede retrasar u omitir ejecuciones.
- Envía cuando todas las citas consultadas están terminadas, canceladas o ausentes y los totales llevan al menos 15 minutos estables. No se deduce que la clínica cerró de una hora de cita sin verificar su estado. Si quedan citas activas, no envía y habrá que corregirlas en Curve.
- Si hay producción pero no citas, espera hasta las 20:00 entre semana o 18:00 sábado, más el intervalo de estabilidad. Sin producción positiva, no envía, igual que el programa anterior.
- Si el último control llega tarde o los totales siguen cambiando al final de la ventana, puede no salir ese día. Revisar la ejecución; no se envía retrospectivamente de forma automática.
- Producción y cobros se suman desde `calculations.production` y `calculations.payments`, con decimales y todas las páginas. El rango de ledger termina al comienzo del día siguiente, exclusivo.
- El antiguo “Patients seen” cuenta pacientes únicos con producción positiva; no cuenta citas en Check Out. Se conserva el cálculo y la plantilla lo nombra “Pacientes con producción”.

## Estado y fallos

Los runners de GitHub son temporales: un archivo en `/tmp` no evita duplicados entre ejecuciones. El nuevo programa guarda una marca por fecha en la rama `daily-summary-state`, con permisos `contents: write` del `GITHUB_TOKEN` de Actions. No necesita otro token de GitHub. Esta rama conserva únicamente fecha, estado y huella de totales; no guarda pacientes ni credenciales. La rama se crea en el primer envío habilitado.

El workflow serializa las ejecuciones y cada escritura verifica el SHA actual. Un fallo al reservar el envío detiene el POST. `accepted` impide repetir la fecha, aunque se vuelva a ejecutar el workflow. `sending` significa que el resultado es incierto o que se interrumpió el programa entre la reserva y la confirmación: no se reintenta automáticamente, para evitar mensajes duplicados. Una respuesta HTTP 4xx explícita (salvo 408) permite volver a intentar tras corregir la causa. Un fallo de red o 5xx conserva el estado incierto.

Para resolver un estado `sending`, comprobar primero el mensaje en Meta o en el destinatario. Si llegó, cambiar el estado a `accepted`. Solo si se confirma que no se envió, cambiarlo a `waiting` en el archivo de esa fecha; el siguiente control vuelve a esperar 15 minutos. No borrar marcas de mensajes aceptados.

No se garantiza entrega exactamente una vez: una respuesta perdida después del POST requiere revisión. El programa prioriza no repetir envíos dudosos y muestra fallo en Actions. No registra un mensaje rechazado como exitoso.

## Pruebas

```bash
python -m unittest discover -s tests -v
```

Las 29 pruebas unitarias usan respuestas simuladas: ledger de doble entrada, paginación, fallos parciales, horario de verano, cierre estable, rechazo de Meta, resultados inciertos, escrituras de estado en conflicto y extracción del token desde la configuración de Google Cloud y envío de un ejemplo independiente con protección contra duplicados. No usan credenciales ni envían mensajes. El workflow `Verify Curve and Meta connections` realiza por separado consultas de lectura con los secretos guardados; nunca envía WhatsApp. Solo se usa la biblioteca estándar de Python 3.11+.

Referencias: [envío de plantillas de Meta](https://www.postman.com/meta/whatsapp-business-platform/request/o65u5m5/send-message-template-text), [Messages de Meta](https://www.postman.com/meta/whatsapp-business-platform/folder/o48mro7/messages), [GitHub Contents API](https://docs.github.com/en/rest/repos/contents), [programación de Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
