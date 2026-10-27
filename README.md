# MD55 Rescue

Recuperación de vídeo de cámaras Sony: XAVC S, XAVC HS, XAVC S-I y XAVC en MXF.
Conserva el flujo original sin recodificar, así que el material en S-Log3, S-Cinetone,
HLG, 10 bits 4:2:2, etc. sale exactamente como lo grabó la cámara.

Funciona en **Windows y macOS**. ffmpeg es opcional: sirve para verificar el resultado y
para re-empaquetar MXF.

## La aplicación

| Sistema | Cómo obtenerla |
|---|---|
| Windows | `dist/MD55-Rescue-windows.zip` → descomprimir → `MD55 Rescue.exe`. O compilar con `build_windows.bat`. |
| macOS | En un Mac: `./build_mac.sh` → `dist/MD55 Rescue.app` y un `.dmg`. O subir el proyecto a GitHub: `.github/workflows/build.yml` compila Windows, Mac Apple Silicon y Mac Intel. |

La app no está firmada: en Windows SmartScreen puede avisar («Más información → Ejecutar de
todas formas»); en Mac, la primera vez clic derecho → Abrir.

Pantallas:

- **Rescatar tarjeta**: elige la tarjeta (o una imagen `.img`) y la carpeta de destino. Al
  leer la tarjeta pide permiso de Administrador (UAC en Windows, contraseña en Mac). Los clips
  aparecen en una tabla a medida que se recuperan, con duración, códec, perfil (S-Log3,
  S-Cinetone…) y fecha. «Detener» funciona en cualquier momento.
- **Reparar clip**: añade los clips que no abren y una carpeta con clips sanos; elige sola la
  referencia compatible (Intra / Long GOP, resolución, bitrate).
- **Analizar clip**: arrastra un clip y muestra su diagnóstico.

En macOS la tarjeta se lee como `/dev/diskN` (el programa la lista sola); en Windows por su
letra (`E:`).

## Desde el código

Con **Python 3.10+** y `pip install -r requirements.txt` (sin PySide6 se abre una interfaz
básica de tkinter):

```bash
python -m xavc_rescue
```

O por consola, desde esta carpeta:

```bash
python -m xavc_rescue analizar C0001.MP4
```

```bash
python -m xavc_rescue reparar C0002.MP4 --referencia C0001.MP4
```

```bash
python -m xavc_rescue rescatar E: -o D:\Rescate --referencia CarpetaReferencias
```

```bash
sudo python3 -m xavc_rescue rescatar /dev/disk4 -o ~/Rescate
```

```bash
python -m xavc_rescue imagen E: D:\tarjeta.img
```

```bash
python -m xavc_rescue mxf CLIP.MXF -o D:\Rescate
```

## Qué hace cada función

| Función | Para qué caso |
|---|---|
| **Analizar** | Muestra la estructura, si falta el índice, si hay zonas a cero, códec, fps y perfil de imagen (S-Log3 / S-Cinetone / HLG) leído del XML de Sony. |
| **Reparar** | Clip que no abre porque la grabación se cortó (batería, tarjeta extraída, apagado, caída). Reconstruye el índice `moov`. |
| **Rescatar** | Clips borrados o tarjeta formateada (formateo rápido). Busca por firma en la tarjeta o en una imagen `.img`; los incompletos los repara si das una referencia. |
| **Imagen** | Copia la tarjeta sector a sector a un `.img` (los sectores ilegibles se rellenan con ceros). Hazlo primero si la tarjeta falla. |
| **MXF** | MXF sin cerrar (FX9, Venice, PXW…): extrae el vídeo y el audio y, con ffmpeg, los vuelve a empaquetar. *Experimental: no probado con MXF reales.* |

## El clip de referencia

Para reparar hace falta un clip **sano** grabado con:

- la **misma cámara** (o el mismo modelo con el mismo firmware),
- el **mismo formato**: XAVC S / HS / S-I, resolución, fps y bitrate (p.ej. *XAVC HS 4K 59.94p 4:2:2 10 bits*),
- la **misma configuración de audio** (canales, LPCM 16/24 bits).

El perfil de imagen (S-Log3, S-Cinetone…) no cambia la estructura del archivo, pero grabar la
referencia con el mismo perfil no cuesta nada. Basta con 5–30 segundos.

## Cómo funciona la reparación

Las Sony escriben `ftyp → uuid → free → mdat → moov → meta`. Si la grabación se corta,
el `mdat` (los datos) está pero falta el `moov` (el índice). Dentro del `mdat` la cámara
intercala bloques: **rtmd** (metadatos por fotograma) → **vídeo** (≈1 s de fotogramas) →
**audio LPCM**, y rellena con ceros hasta múltiplos de 64 MB.

El programa:

1. Aprende de la referencia el orden de bloques, cuántos fotogramas lleva cada uno, el tamaño
   del audio y del rtmd por fotograma y cómo empiezan los bloques.
2. Recorre el `mdat` dañado fotograma a fotograma leyendo las unidades NAL H.264/H.265,
   comprobando cada bloque con el siguiente y resincronizando si encuentra datos ilegibles.
3. Calcula el orden de presentación real (POC) de cada fotograma (Long GOP con B-frames).
4. Escribe un `moov` nuevo y copia los datos tal cual.

## Probado con

Clips reales de una **ILCE-9M3** en S-Log3 / S-Gamut3.Cine, 59.94p, cortados artificialmente:

| Códec | Fotogramas recuperados idénticos al original |
|---|---|
| XAVC HS 4K 4:2:2 10 bits (HEVC) | 100 % de los presentes |
| XAVC S 4K 200 Mbps 4:2:2 Long GOP | 100 % |
| XAVC S HD 50 Mbps 4:2:2 Long GOP | 100 % |
| XAVC S-I 4K / HD 100 Mbps 4:2:2 Intra | 100 % |

Y una imagen de tarjeta simulada: el clip completo se recuperó byte a byte y el cortado se reparó.
Se pierde como mucho el último bloque (< 1 s) si quedó a medias.

## Tarjetas formateadas: cómo las deja la cámara

Comprobado en una ILCE-9M3 (A9 III): en la tarjeta cada clip no queda en un solo bloque.

```
[ftyp/uuid/free + cabecera mdat]  [hueco 1–1.5 MB]  [datos de vídeo/audio/rtmd]  [moov + meta]
```

El índice (`moov`) y el XML con el perfil (`meta`) quedan al final de la reserva del clip,
justo antes del siguiente. Tras un formateo rápido el programa empareja cada índice con su
cabecera (comprobando que los tamaños de fotograma del índice encajan exactamente con los
datos), salta el hueco y vuelve a montar el archivo original byte a byte.

## Límites honestos

- Si la tarjeta está **fragmentada** (se grabó y borró mucho sin formatear), un clip borrado puede
  estar en trozos no contiguos; el rescate por firma lo recupera solo hasta el primer salto.
- Un formateo **completo** (sobrescritura) o un borrado seguro no se puede recuperar.
- Los clips proxy (AAC) no se reparan: su audio no tiene tamaño fijo.
- Para leer la tarjeta directamente (`E:`) hay que abrir el programa **como Administrador**.
- Nunca guardes los resultados en la misma tarjeta.
