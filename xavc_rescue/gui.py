"""Interfaz gráfica (tkinter, incluida con Python)."""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, analyze, carve, ffmpeg, mxf, repair

VIDEO_TYPES = [('Clips Sony', '*.MP4 *.mp4 *.MXF *.mxf *.MOV *.mov'), ('Todos', '*.*')]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MD55 Rescue %s — recuperación de vídeo Sony' % __version__)
        self.geometry('900x640')
        self.minsize(720, 520)
        self.q = queue.Queue()
        self.busy = False

        nb = ttk.Notebook(self)
        nb.pack(fill='x', padx=8, pady=(8, 0))
        nb.add(self._tab_analyze(nb), text='1. Analizar')
        nb.add(self._tab_repair(nb), text='2. Reparar clip interrumpido')
        nb.add(self._tab_carve(nb), text='3. Rescatar de tarjeta')
        nb.add(self._tab_mxf(nb), text='4. MXF sin cerrar')

        self.progress = ttk.Progressbar(self, mode='determinate', maximum=1000)
        self.progress.pack(fill='x', padx=8, pady=6)
        frame = ttk.Frame(self)
        frame.pack(fill='both', expand=True, padx=8, pady=(0, 8))
        self.text = tk.Text(frame, wrap='word', font=('Consolas', 10))
        sb = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.text.pack(side='left', fill='both', expand=True)
        self.log('Consejo: deja de grabar en la tarjeta afectada. Guarda siempre los resultados '
                 'en OTRO disco.')
        if not ffmpeg.find():
            self.log('ffmpeg no encontrado: la verificación y el re-empaquetado MXF son opcionales '
                     '(instálalo con: winget install Gyan.FFmpeg).')
        self.after(100, self._poll)

    # ------------------------------------------------------------ helpers
    def _row(self, parent, label, var, browse):
        r = ttk.Frame(parent)
        r.pack(fill='x', pady=3)
        ttk.Label(r, text=label, width=24).pack(side='left')
        ttk.Entry(r, textvariable=var).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(r, text='Elegir...', command=browse).pack(side='left')

    def _open(self, var):
        return lambda: var.set(filedialog.askopenfilename(filetypes=VIDEO_TYPES) or var.get())

    def _save(self, var):
        return lambda: var.set(filedialog.asksaveasfilename(
            defaultextension='.MP4', filetypes=VIDEO_TYPES) or var.get())

    def _dir(self, var):
        return lambda: var.set(filedialog.askdirectory() or var.get())

    def log(self, msg):
        self.q.put(('log', msg))

    def set_progress(self, p):
        self.q.put(('progress', p))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == 'log':
                    self.text.insert('end', val + '\n')
                    self.text.see('end')
                elif kind == 'progress':
                    self.progress['value'] = int(val * 1000)
                elif kind == 'done':
                    self.busy = False
                    self.progress['value'] = 0
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def run_task(self, fn):
        if self.busy:
            messagebox.showinfo('Ocupado', 'Espera a que termine la tarea actual.')
            return
        self.busy = True
        self.text.insert('end', '\n' + '─' * 70 + '\n')

        def worker():
            try:
                fn()
            except PermissionError:
                self.log('ERROR: sin permiso. Para leer la tarjeta directamente abre el programa '
                         'como Administrador.')
            except Exception as e:   # noqa: BLE001 — se muestra al usuario
                self.log('ERROR: %s' % e)
            finally:
                self.q.put(('done', None))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ pestañas
    def _tab_analyze(self, nb):
        f = ttk.Frame(nb, padding=10)
        v = tk.StringVar()
        self._row(f, 'Clip (MP4 / MXF):', v, self._open(v))
        ttk.Label(f, text='Muestra la estructura, si falta el índice, el códec y el perfil '
                         '(S-Log3, S-Cinetone, HLG...).').pack(anchor='w', pady=4)
        ttk.Button(f, text='Analizar', command=lambda: self.run_task(
            lambda: self.log(analyze.analyze(v.get())))).pack(anchor='e')
        return f

    def _tab_repair(self, nb):
        f = ttk.Frame(nb, padding=10)
        broken, ref, out = tk.StringVar(), tk.StringVar(), tk.StringVar()
        force = tk.BooleanVar()

        def pick_broken():
            p = filedialog.askopenfilename(filetypes=VIDEO_TYPES)
            if p:
                broken.set(p)
                if not out.get():
                    out.set(os.path.splitext(p)[0] + '_REPARADO.MP4')

        self._row(f, 'Clip dañado:', broken, pick_broken)
        self._row(f, 'Clip de referencia (sano):', ref, self._open(ref))
        self._row(f, 'Guardar como:', out, self._save(out))
        ttk.Label(f, foreground='#555', wraplength=820, text=(
            'La referencia debe ser de la MISMA cámara con el MISMO formato de archivo, resolución, '
            'fps, bitrate (p.ej. XAVC S 4K 100M 23.98p) y audio. El perfil de imagen no cambia la '
            'estructura, pero lo ideal es grabar la referencia también en S-Log3/S-Cinetone. '
            'Basta con 10–30 segundos.')).pack(anchor='w', pady=4)
        ttk.Checkbutton(f, text='Forzar (reconstruir aunque el clip ya tenga índice)',
                        variable=force).pack(anchor='w')

        def go():
            def task():
                repair.repair(broken.get(), ref.get(), out.get(), log=self.log,
                              progress=self.set_progress, force=force.get())
                ffmpeg.verify(out.get(), log=self.log)
            self.run_task(task)

        ttk.Button(f, text='Reparar', command=go).pack(anchor='e')
        return f

    def _tab_carve(self, nb):
        f = ttk.Frame(nb, padding=10)
        src, dst, ref = tk.StringVar(), tk.StringVar(), tk.StringVar()

        def pick_img():
            p = filedialog.askopenfilename(filetypes=[('Imagen de disco', '*.img *.dd *.bin'),
                                                      ('Todos', '*.*')])
            if p:
                src.set(p)

        self._row(f, 'Tarjeta (E:) o imagen:', src, pick_img)
        self._row(f, 'Carpeta de salida:', dst, self._dir(dst))
        self._row(f, 'Referencia (opcional):', ref, self._open(ref))
        ttk.Label(f, foreground='#555', wraplength=820, text=(
            'Escribe la letra de la tarjeta (p.ej. E:) para leerla directamente (requiere '
            'Administrador) o usa una imagen .img. Lo más seguro es crear primero la imagen y '
            'rescatar desde ella. La salida debe estar en otro disco.')).pack(anchor='w', pady=4)
        b = ttk.Frame(f)
        b.pack(anchor='e')

        def make_image():
            p = filedialog.asksaveasfilename(defaultextension='.img',
                                             filetypes=[('Imagen de disco', '*.img')])
            if p:
                self.run_task(lambda: carve.make_image(src.get(), p, log=self.log,
                                                       progress=self.set_progress))

        ttk.Button(b, text='Crear imagen .img', command=make_image).pack(side='left', padx=4)
        ttk.Button(b, text='Buscar y rescatar', command=lambda: self.run_task(
            lambda: carve.carve(src.get(), dst.get(), log=self.log, progress=self.set_progress,
                                reference=ref.get() or None))).pack(side='left')
        return f

    def _tab_mxf(self, nb):
        f = ttk.Frame(nb, padding=10)
        src, dst = tk.StringVar(), tk.StringVar()
        self._row(f, 'Clip MXF:', src, self._open(src))
        self._row(f, 'Carpeta de salida:', dst, self._dir(dst))
        ttk.Label(f, foreground='#555', wraplength=820, text=(
            'Para XAVC en MXF (FX9, Venice, PXW...). Si el archivo quedó sin cerrar, extrae el '
            'vídeo y el audio y, con ffmpeg, los vuelve a empaquetar en un .mov.')).pack(anchor='w', pady=4)

        def task():
            out_dir = dst.get() or os.path.dirname(src.get())
            base = os.path.splitext(os.path.basename(src.get()))[0]
            mov = os.path.join(out_dir, base + '_RECUPERADO.mov')
            if not ffmpeg.remux(src.get(), mov, log=self.log):
                res = mxf.extract(src.get(), out_dir, log=self.log)
                ffmpeg.mux_mxf_parts(res['video'], res['audio'], res['fps'], mov, log=self.log)

        ttk.Button(f, text='Extraer', command=lambda: self.run_task(task)).pack(anchor='e')
        return f


def main():
    App().mainloop()
    return 0
