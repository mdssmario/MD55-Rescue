"""Línea de comandos:

  xavc_rescue analizar C0001.MP4
  xavc_rescue reparar C0002.MP4 --referencia C0001.MP4      (o una carpeta de referencias)
  xavc_rescue rescatar E: -o D:\\Rescate [--referencia CARPETA] [--comparar CARPETA]
  xavc_rescue rescatar /dev/disk4 -o ~/Rescate                (macOS, con sudo)
  xavc_rescue imagen E: D:\\tarjeta.img
  xavc_rescue mxf CLIP.MXF -o D:\\Rescate

Herramientas de tarjeta:
  xavc_rescue tarjeta H:                     información y salud
  xavc_rescue probar-lectura H:              lee toda la tarjeta (no escribe)
  xavc_rescue probar-capacidad H:            llena el espacio libre y lo verifica
  xavc_rescue reparar-fs H:                  chkdsk / diskutil repairVolume
  xavc_rescue formatear H: --confirmar H:    BORRA TODO y formatea al estilo SD

Con --eventos ARCHIVO el progreso se escribe como líneas JSON (lo usa la interfaz).
"""
import argparse
import os
import sys

from . import analyze, cardtools, carve, ffmpeg, mxf, repair


def _bar(p):
    sys.stdout.write('\r  progreso: %3d %%' % int(p * 100))
    sys.stdout.flush()
    if p >= 1:
        sys.stdout.write('\n')


def _card_command(args, log, progress, should_stop, on_result):
    if args.cmd == 'probar-capacidad' and args.carpeta:
        res = cardtools.capacity_test(args.origen, log, progress, should_stop,
                                      int(args.max_gb * cardtools.GB) if args.max_gb else None)
        on_result(res)
        return
    card = cardtools.info(args.origen)
    log('%s · %s · %.1f GB · %s %s' % (card['id'], card['model'], card['size'] / cardtools.GB,
                                       card.get('style') or '', card.get('fs') or ''))
    if args.cmd == 'tarjeta':
        h = cardtools.health(card)
        log(h['conclusion'])
        on_result({'tarjeta': card, 'salud': h})
    elif args.cmd == 'probar-lectura':
        on_result(cardtools.surface_scan(args.origen, log, progress, should_stop))
    elif args.cmd == 'probar-capacidad':
        cardtools.safety_check(card, allow_fixed=args.no_extraible)
        root = cardtools.mount_point(card)
        if not root:
            raise cardtools.CardError('La tarjeta no está montada: no se puede escribir la prueba.')
        on_result(cardtools.capacity_test(root, log, progress, should_stop,
                                          int(args.max_gb * cardtools.GB) if args.max_gb else None))
    elif args.cmd == 'reparar-fs':
        cardtools.safety_check(card, allow_fixed=args.no_extraible)
        on_result({'codigo': cardtools.fs_repair(card, log)})
    elif args.cmd == 'formatear':
        if args.confirmar.strip().rstrip('\\').upper() != args.origen.strip().rstrip('\\').upper():
            raise cardtools.CardError('La confirmación no coincide con la tarjeta: no se formatea nada.')
        fs, unit = cardtools.reformat(card, args.etiqueta, log, args.permitir_virtual, args.simular,
                                      args.no_extraible)
        if not args.simular:
            on_result({'fs': fs, 'cluster': unit})


def main(argv=None):
    ap = argparse.ArgumentParser(prog='xavc_rescue',
                                 description='Recuperación de vídeo de cámaras Sony (XAVC S/HS/S-I, MXF).')
    sub = ap.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('analizar', help='diagnosticar un clip')
    a.add_argument('archivo')

    r = sub.add_parser('reparar', help='reconstruir el índice de un clip interrumpido')
    r.add_argument('archivo')
    r.add_argument('--referencia', '-r', required=True,
                   help='clip SANO de la misma cámara y configuración, o carpeta con varios')
    r.add_argument('-o', '--salida')
    r.add_argument('--forzar', action='store_true', help='reconstruir aunque ya tenga índice')

    c = sub.add_parser('rescatar', help='buscar clips borrados en tarjeta o imagen')
    c.add_argument('origen', help='E:, /dev/disk4 o archivo .img')
    c.add_argument('-o', '--salida', required=True)
    c.add_argument('--referencia', '-r', help='clip o carpeta de referencia para reparar incompletos')
    c.add_argument('--comparar', metavar='CARPETA',
                   help='parar al encontrar los clips de esta carpeta y comprobar que son idénticos')
    c.add_argument('--desde', type=float, default=0, metavar='GB', help='empezar en este punto (GB)')
    c.add_argument('--hasta', type=float, metavar='GB', help='terminar en este punto (GB)')
    c.add_argument('--tipos', default='video,foto',
                   help='qué buscar: video, foto o video,foto (por defecto, ambos)')

    i = sub.add_parser('imagen', help='copiar la tarjeta a un archivo .img')
    i.add_argument('origen')
    i.add_argument('destino')

    m = sub.add_parser('mxf', help='extraer vídeo/audio de un MXF sin cerrar')
    m.add_argument('archivo')
    m.add_argument('-o', '--salida')

    t = sub.add_parser('tarjeta', help='información y salud de una tarjeta')
    t.add_argument('origen')
    sl = sub.add_parser('probar-lectura', help='leer toda la tarjeta buscando sectores dañados')
    sl.add_argument('origen')
    pc = sub.add_parser('probar-capacidad', help='llenar el espacio libre y verificarlo (H2testw)')
    pc.add_argument('origen', help='tarjeta (H:, /dev/disk4) o, con --carpeta, una carpeta')
    pc.add_argument('--max-gb', type=float, help='probar solo esta cantidad')
    pc.add_argument('--carpeta', action='store_true', help='el origen es una carpeta (pruebas)')
    rf = sub.add_parser('reparar-fs', help='comprobar y reparar el sistema de archivos')
    rf.add_argument('origen')
    fm = sub.add_parser('formatear', help='BORRA TODO: partición y formato nuevos al estilo SD')
    fm.add_argument('origen')
    fm.add_argument('--confirmar', required=True, help='repite el origen para confirmar')
    fm.add_argument('--etiqueta', default='MD55')
    fm.add_argument('--simular', action='store_true', help='mostrar los pasos sin ejecutar nada')
    for p in (pc, rf, fm):
        p.add_argument('--no-extraible', action='store_true',
                       help='permitir un lector que el sistema no marca como extraíble')
    fm.add_argument('--permitir-virtual', action='store_true', help=argparse.SUPPRESS)

    for p in (a, r, c, i, m, t, sl, pc, rf, fm):
        p.add_argument('--eventos', help=argparse.SUPPRESS)

    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        # la consola de Windows (cp1252) no puede escribir todos los caracteres del registro
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    sink = None
    log, progress, on_clip, should_stop = print, _bar, None, None
    on_result = lambda data: None   # noqa: E731
    if args.eventos:
        from .events import EventFile
        sink = EventFile(args.eventos)
        log, progress, on_clip, should_stop = sink.log, sink.progress, sink.clip, sink.stop_requested
        on_result = sink.result
    try:
        if args.cmd in ('tarjeta', 'probar-lectura', 'probar-capacidad', 'reparar-fs', 'formatear'):
            _card_command(args, log, progress, should_stop, on_result)
        elif args.cmd == 'analizar':
            log(analyze.analyze(args.archivo))
        elif args.cmd == 'reparar':
            out = args.salida or os.path.splitext(args.archivo)[0] + '_REPARADO.MP4'
            res = repair.repair(args.archivo, args.referencia, out, log=log, progress=progress,
                                force=args.forzar)
            ffmpeg.verify(out, log=log)
            if on_clip:
                from . import clipinfo
                on_clip(dict(path=out, complete=True, note='reparado', size=os.path.getsize(out),
                             frames=res['frames'], **clipinfo.summary(out)))
        elif args.cmd == 'rescatar':
            carve.carve(args.origen, args.salida, log=log, progress=progress,
                        reference=args.referencia, compare_dir=args.comparar,
                        on_clip=on_clip, should_stop=should_stop,
                        start=int(args.desde * 1e9), end=int(args.hasta * 1e9) if args.hasta else None,
                        kinds=tuple(t.strip() for t in args.tipos.split(',')))
        elif args.cmd == 'imagen':
            carve.make_image(args.origen, args.destino, log=log, progress=progress,
                             should_stop=should_stop)
        elif args.cmd == 'mxf':
            out_dir = args.salida or os.path.dirname(os.path.abspath(args.archivo))
            base = os.path.splitext(os.path.basename(args.archivo))[0]
            mov = os.path.join(out_dir, base + '_RECUPERADO.mov')
            if not ffmpeg.remux(args.archivo, mov, log=log):
                res = mxf.extract(args.archivo, out_dir, log=log)
                ffmpeg.mux_mxf_parts(res['video'], res['audio'], res['fps'], mov, log=log)
    except (repair.RepairError, cardtools.CardError, ValueError, OSError) as e:
        msg = str(e)
        if isinstance(e, PermissionError):
            msg += ' — para leer una tarjeta directamente hacen falta permisos de Administrador.'
        if sink:
            sink.done(False, msg)
        else:
            print('ERROR: %s' % msg, file=sys.stderr)
        return 1
    except Exception as e:          # noqa: BLE001 — cualquier fallo debe llegar a la interfaz
        if sink:
            sink.done(False, 'Error inesperado: %r' % e)
            return 1
        raise
    if sink:
        sink.done(True)
    return 0
