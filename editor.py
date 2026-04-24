import os
import cv2
import copy
import uuid
import zipfile
from tkinter import filedialog
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk, ImageDraw
import numpy as np
import math
import matplotlib.font_manager as fm
from image_processor import ImageProcessor

def get_line_intersection(p1, v1, p2, v2):
    cross = v1[0]*v2[1] - v1[1]*v2[0]
    if abs(cross) < 1e-6: return None
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    t1 = (dx*v2[1] - dy*v2[0]) / cross
    return (p1[0] + t1*v1[0], p1[1] + t1*v1[1])

def is_valid_poly(pts):
    if len(pts) < 2: return True
    for i in range(len(pts)):
        d = math.hypot(pts[i][0] - pts[(i+1)%len(pts)][0], pts[i][1] - pts[(i+1)%len(pts)][1])
        if d < 3: return False
    return True

class ConflictDialog(tk.Toplevel):
    def __init__(self, parent, filename):
        super().__init__(parent)
        self.title("Konflikt plików")
        self.geometry("380x160")
        self.choice = "skip"
        self.apply_to_all = False

        tk.Label(self, text=f"Plik '{filename}' już istnieje w docelowym folderze.\nWybierz co zrobić:", justify=tk.CENTER).pack(pady=10)

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Zastąp", width=10, command=lambda: self.set_choice("replace")).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Pomiń", width=10, command=lambda: self.set_choice("skip")).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Zmień nazwę", width=12, command=lambda: self.set_choice("keep")).pack(side=tk.LEFT, padx=5)

        self.check_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="Zastosuj tę decyzję do wszystkich konfliktów", variable=self.check_var).pack(pady=10)

        self.transient(parent)
        self.grab_set()

        parent.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - 190
        y = parent.winfo_y() + (parent.winfo_height() // 2) - 80
        self.geometry(f"+{x}+{y}")

    def set_choice(self, choice):
        self.choice = choice
        self.apply_to_all = self.check_var.get()
        self.destroy()

def get_system_fonts():
    font_map = {}
    raw_fonts = {}
    weight_map = {
        '100': 'Thin', '200': 'Extra Light', '300': 'Light',
        '400': 'Regular', '500': 'Medium', '600': 'Semi Bold',
        '700': 'Bold', '800': 'Extra Bold', '900': 'Black',
        'normal': 'Regular', 'bold': 'Bold', 'light': 'Light'
    }
    try:
        for font in fm.fontManager.ttflist:
            family = font.name
            path = font.fname
            ext = path.lower().split('.')[-1]
            if ext not in ['ttf', 'otf']: continue
            w = str(font.weight).lower()
            w_name = weight_map.get(w, str(font.weight).title())
            style = "Italic" if font.style in ['italic', 'oblique'] else ""
            variant = f"{w_name} {style}".strip()
            if variant == "": variant = "Regular"
            if variant == "Regular Italic": variant = "Italic"
            unique_name = f"{family} - {variant}"
            if unique_name in font_map:
                if ext == 'ttf' and font_map[unique_name].lower().endswith('.otf'):
                    font_map[unique_name] = path
            else:
                font_map[unique_name] = path
            if family not in raw_fonts:
                raw_fonts[family] = {}
            if variant in raw_fonts[family]:
                if ext == 'ttf' and font_map[unique_name].lower().endswith('.otf'):
                     raw_fonts[family][variant] = unique_name
            else:
                 raw_fonts[family][variant] = unique_name
    except:
        font_map = {"Arial - Regular": "arial.ttf"}
        raw_fonts = {"Arial": {"Regular": "Arial - Regular"}}

    menu_structure = {}
    for family in sorted(raw_fonts.keys()):
        letter = family[0].upper()
        if not ('A' <= letter <= 'Z'): letter = "Pozostałe"
        if letter not in menu_structure: menu_structure[letter] = {}
        variants = raw_fonts[family]
        sorted_variants = sorted(variants.keys(), key=lambda x: (0 if x in ['Regular', 'Normal'] else 1, x))
        menu_structure[letter][family] = [(v, variants[v]) for v in sorted_variants]

    return font_map, menu_structure

FONT_MAP, GROUPED_FONTS = get_system_fonts()

class EditorWindow:
    def __init__(self, parent_frame, input_dir, engine, auto_translate, on_close_callback, original_zip_path=None):
        self.frame = parent_frame
        self.input_dir = input_dir
        self.engine = engine
        self.auto_translate = auto_translate
        self.on_close = on_close_callback
        self.original_zip_path = original_zip_path

        self.output_dir = os.path.join(input_dir, "przetlumaczone")
        if not self.original_zip_path and not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

        self.image_files = []
        for root_dir, _, files in os.walk(input_dir):
            for f in files:
                if f.lower().endswith(('.tif', '.png', '.jpg', '.jpeg')):
                    self.image_files.append(os.path.relpath(os.path.join(root_dir, f), input_dir))

        self.current_index = 0

        self.processors = {}
        self.source_thumbnails = []
        self.thumbnails = []
        self.gallery_widgets = []
        self.thumbnail_size = 120

        self.selected_box = None
        self.dragged_vertex_idx = None
        self.dragged_edge_idx = None
        self.dragged_box = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_start_points = []

        self.scale = 1.0
        self.base_scale = 1.0
        self.offset_x = 0; self.offset_y = 0

        self.is_panning = False
        self.pan_last_x = 0
        self.pan_last_y = 0
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        self.drawing_poly_mode = False
        self.current_poly_points = []
        self.drawing_rect_mode = False
        self.temp_lines = []

        self._is_updating_sidebar = False
        self._typing_timer = None
        self.fast_delete_var = tk.BooleanVar(value=False)
        self.clipboard_box = None

        self.format_source_box = None
        self.size_source_box = None
        self.aio_source_box = None

        self.last_font_size = 24
        self.last_line_spacing = 2

        # SYSTEM UNDO/REDO
        self.global_history = []
        self.history_index = -1

        self.create_menu()
        self.process_all_images()

    def show_author(self):
        messagebox.showinfo(
            "Autor",
            "Tłumacz OCR\n\n"
            "Autor: Mateusz Bojarski\n"
            "Do wewnętrznego użytku w AOMB Polska Sp. z o.o.\n"
            "Wersja 1.0"
        )

    def create_menu(self):
        root = self.frame.winfo_toplevel()
        menubar = tk.Menu(root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Zakończ i wróć", command=self.on_close)
        file_menu.add_command(label="Wyjdź z aplikacji", command=root.quit)
        menubar.add_cascade(label="Plik", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Autor", command=self.show_author)
        menubar.add_cascade(label="O programie", menu=help_menu)

        root.config(menu=menubar)

    def process_all_images(self):
        if not self.image_files:
            messagebox.showinfo("Informacja", "Brak obrazków we wskazanym źródle.")
            self.on_close()
            return

        # --- SPRAWDZANIE MODELI OCR ---
        if self.engine == "paddleocr":
            paddle_path = os.path.join(os.path.expanduser("~"), ".paddleocr")
            if not os.path.exists(paddle_path):
                ans = messagebox.askokcancel("Wymagane pobieranie modelu",
                                             "Model sztucznej inteligencji PaddleOCR nie został jeszcze pobrany na ten komputer.\n\n"
                                             "Zajmie on ok. 15 MB miejsca na dysku i zostanie zapisany w katalogu:\n" + paddle_path + "\n\n"
                                             "Czy chcesz kontynuować i pobrać model?")
                if not ans:
                    self.on_close()
                    return
        elif self.engine == "easyocr":
            easy_path = os.path.join(os.path.expanduser("~"), ".EasyOCR")
            if not os.path.exists(easy_path):
                ans = messagebox.askokcancel("Wymagane pobieranie modelu",
                                             "Model sztucznej inteligencji EasyOCR nie został jeszcze pobrany na ten komputer.\n\n"
                                             "Zajmie on ok. 40-70 MB miejsca na dysku i zostanie zapisany w katalogu:\n" + easy_path + "\n\n"
                                             "Czy chcesz kontynuować i pobrać model?")
                if not ans:
                    self.on_close()
                    return

        for widget in self.frame.winfo_children():
            if isinstance(widget, tk.Button) and widget.cget("text") == "Rozpocznij przetwarzanie":
                widget.config(state=tk.DISABLED, text="Przetwarzanie trwa... (Proszę czekać)")

        root_win = self.frame.winfo_toplevel()
        root_win.geometry("450x580")
        root_win.title("Tłumacz OCR")

        self.progress_frame = tk.Frame(self.frame, pady=15)
        self.progress_frame.pack(side=tk.BOTTOM, fill=tk.X)

        tk.Label(self.progress_frame, text="Inicjalizacja i analiza obrazów...", font=("Arial", 11, "bold"), fg="#0052cc").pack()
        progress_bar = ttk.Progressbar(self.progress_frame, orient="horizontal", length=350, mode="determinate")
        progress_bar.pack(pady=5)
        progress_bar["maximum"] = len(self.image_files)

        self.lbl_info = tk.Label(self.progress_frame, text="", font=("Arial", 9))
        self.lbl_info.pack()
        self.lbl_count = tk.Label(self.progress_frame, text=f"0 / {len(self.image_files)}", font=("Arial", 9, "bold"))
        self.lbl_count.pack()
        root_win.update()

        for i, filename in enumerate(self.image_files):
            self.lbl_info.config(text=f"Analiza: {filename}")
            self.lbl_count.config(text=f"{i+1} / {len(self.image_files)}")
            progress_bar["value"] = i + 1
            root_win.update()

            img_path = os.path.join(self.input_dir, filename)
            try:
                processor = ImageProcessor(img_path, self.engine, self.auto_translate)
                processor.detect_text()
                self.processors[filename] = processor

                pil_img = Image.fromarray(cv2.cvtColor(processor.original_cv_image, cv2.COLOR_BGR2RGB))
                pil_img.thumbnail((300, 300), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
                self.source_thumbnails.append(pil_img)
            except Exception as e:
                print(f"Pominięto plik {filename} z powodu błędu: {e}")
                self.image_files.remove(filename)

        self.progress_frame.destroy()
        root_win.geometry("1300x800")

        if self.image_files:
            self.setup_ui()
            self._save_global_state() # STARTOWY PUNKT DLA UNDO (Indeks 0)
            self.select_image(0, auto_scroll=True)

            root_win.bind("<Delete>", self.handle_delete_key)
            root_win.bind("<Control-c>", self.copy_box)
            root_win.bind("<Control-v>", self.paste_box)
            root_win.bind("<v>", self.handle_v_key)
            root_win.bind("<V>", self.handle_v_key)
            root_win.bind("<c>", self.handle_c_key)
            root_win.bind("<C>", self.handle_c_key)
            root_win.bind("<x>", self.handle_x_key)
            root_win.bind("<X>", self.handle_x_key)

            # Podpinanie Cofania
            root_win.bind("<Control-z>", self.undo)
            root_win.bind("<Control-Z>", self.undo)
            root_win.bind("<Control-y>", self.redo)
            root_win.bind("<Control-Y>", self.redo)
            root_win.bind("<Control-Alt-z>", self.redo)
            root_win.bind("<Control-Alt-Z>", self.redo)
        else:
            self.on_close()

    # --- SYSTEM UNDO / REDO ---
    def _save_global_state(self):
        state = {
            'filename': self.image_files[self.current_index],
            'boxes_dict': {fn: copy.deepcopy(p.boxes) for fn, p in self.processors.items()}
        }
        self.global_history = self.global_history[:self.history_index + 1]
        self.global_history.append(state)

        if len(self.global_history) > 11: # Maksymalnie 10 operacji w pamięci
            self.global_history.pop(0)

        self.history_index = len(self.global_history) - 1

    def undo(self, event=None):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox)):
            return

        if self.history_index > 0:
            self.history_index -= 1
            self._restore_global_state()

    def redo(self, event=None):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox)):
            return

        if self.history_index < len(self.global_history) - 1:
            self.history_index += 1
            self._restore_global_state()

    def _restore_global_state(self):
        state = self.global_history[self.history_index]
        for fn, boxes in state['boxes_dict'].items():
            self.processors[fn].boxes = copy.deepcopy(boxes)
            self.processors[fn].image_changed = True

        target_file = state['filename']
        if self.image_files[self.current_index] != target_file:
            self.current_index = self.image_files.index(target_file)
            self.processor = self.processors[target_file]
            self.frame.master.title(f"Tłumacz OCR - {target_file} ({self.current_index + 1}/{len(self.image_files)})")
            self.update_gallery_highlight(auto_scroll=True)

        self._set_selected_box(None)
        self.update_sidebar()
        self.redraw_canvas()

    # --- UI ---
    def setup_ui(self):
        for widget in self.frame.winfo_children(): widget.destroy()

        self.canvas = tk.Canvas(self.frame, cursor="cross", bg="#333333")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel_canvas)

        self.canvas_zoom_frame = tk.Frame(self.frame, bg="#e0e0e0", bd=1, relief=tk.RAISED)
        self.canvas_zoom_frame.place(relx=0.0, rely=1.0, anchor='sw', x=10, y=-10)

        top_ctrl_frame = tk.Frame(self.canvas_zoom_frame, bg="#e0e0e0")
        top_ctrl_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5,0))

        tk.Label(top_ctrl_frame, text="Krycie zazn. pola:", bg="#e0e0e0", font=("Arial", 8)).pack(side=tk.LEFT)
        self.alpha_var = tk.DoubleVar(value=100.0)
        self.alpha_slider = ttk.Scale(top_ctrl_frame, from_=0, to=100, orient=tk.HORIZONTAL, variable=self.alpha_var, command=self.on_alpha_slide)
        self.alpha_slider.pack(side=tk.LEFT, padx=(2, 10))

        self.show_text_var = tk.BooleanVar(value=True)
        self.show_boxes_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top_ctrl_frame, text="Podgląd tłumaczenia", variable=self.show_text_var, command=self.toggle_text_preview, bg="#e0e0e0", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)
        tk.Checkbutton(top_ctrl_frame, text="Pokaż ramki", variable=self.show_boxes_var, command=self.redraw_canvas, bg="#e0e0e0", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        bot_ctrl_frame = tk.Frame(self.canvas_zoom_frame, bg="#e0e0e0")
        bot_ctrl_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(2,5))

        tk.Label(bot_ctrl_frame, text="Powiększenie obrazu:", bg="#e0e0e0", font=("Arial", 8)).pack(side=tk.LEFT)
        self.canvas_zoom_var = tk.DoubleVar(value=100.0)
        self.canvas_zoom_slider = ttk.Scale(bot_ctrl_frame, from_=20, to=500, orient=tk.HORIZONTAL, variable=self.canvas_zoom_var, command=lambda e: self.redraw_canvas())
        self.canvas_zoom_slider.pack(side=tk.LEFT, padx=(2, 10))
        tk.Button(bot_ctrl_frame, text="Reset", font=("Arial", 7), command=self.reset_zoom).pack(side=tk.LEFT)

        self.sidebar_container = tk.Frame(self.frame, width=340, bg="#f0f0f0")
        self.sidebar_container.pack(side=tk.RIGHT, fill=tk.Y)
        self.sidebar_container.pack_propagate(False)

        bottom_container = tk.Frame(self.sidebar_container, bg="#f0f0f0")
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

        nav_frame = tk.Frame(bottom_container, bg="#f0f0f0")
        nav_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Button(nav_frame, text="<- Poprzedni", command=self.prev_image, font=("Arial", 9, "bold")).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(nav_frame, text="Następny ->", command=self.next_image, font=("Arial", 9, "bold")).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        export_frame = tk.Frame(bottom_container, bg="#f0f0f0")
        export_frame.pack(fill=tk.X, pady=(0, 5))
        tk.Button(export_frame, text="Eksportuj Bieżący", command=self.export_current, bg="#4CAF50", fg="white").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(export_frame, text="Eksport Wszystkie", command=self.export_all, bg="#2196F3", fg="white").pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        tk.Button(bottom_container, text="Zakończ i Wyjdź", command=self.on_close).pack(fill=tk.X)

        self.paned_window = tk.PanedWindow(self.sidebar_container, orient=tk.VERTICAL, sashwidth=6, sashrelief=tk.RAISED, bg="#cccccc")
        self.paned_window.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.top_pane = tk.Frame(self.paned_window, bg="#f0f0f0")
        self.paned_window.add(self.top_pane, minsize=400, stretch="always")

        self.tools_canvas = tk.Canvas(self.top_pane, bg="#f0f0f0", highlightthickness=0)
        self.tools_scrollbar = ttk.Scrollbar(self.top_pane, orient="vertical", command=self.tools_canvas.yview)
        self.tools_inner = tk.Frame(self.tools_canvas, bg="#f0f0f0", padx=5, pady=5)

        self.tools_window = self.tools_canvas.create_window((0, 0), window=self.tools_inner, anchor="nw")

        def on_tools_canvas_configure(event):
            self.tools_canvas.itemconfig(self.tools_window, width=event.width)
            req_h = self.tools_inner.winfo_reqheight()
            new_h = max(req_h, event.height)
            self.tools_canvas.itemconfig(self.tools_window, height=new_h)
            self.tools_canvas.configure(scrollregion=self.tools_canvas.bbox("all"))

        self.tools_canvas.bind("<Configure>", on_tools_canvas_configure)
        self.tools_inner.bind("<Configure>", lambda e: self.tools_canvas.configure(scrollregion=self.tools_canvas.bbox("all")))
        self.tools_canvas.configure(yscrollcommand=self.tools_scrollbar.set)

        self.tools_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tools_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tools_canvas.bind_all("<MouseWheel>", self._on_mousewheel_tools)

        btn_frame_painters = tk.Frame(self.tools_inner, bg="#f0f0f0")
        btn_frame_painters.pack(fill=tk.X, pady=(0, 5))

        self.btn_format_painter = tk.Button(btn_frame_painters, text="🖌️ Format (V)", command=self.toggle_format_painter, bg="#e0e0e0", font=("Arial", 8))
        self.btn_format_painter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 1))

        self.btn_size_painter = tk.Button(btn_frame_painters, text="📏 Rozmiar (C)", command=self.toggle_size_painter, bg="#e0e0e0", font=("Arial", 8))
        self.btn_size_painter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(1, 1))

        self.btn_aio_painter = tk.Button(btn_frame_painters, text="✨ AIO (X)", command=self.toggle_aio_painter, bg="#e0e0e0", font=("Arial", 8))
        self.btn_aio_painter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(1, 0))

        draw_btn_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        draw_btn_frame.pack(fill=tk.X, pady=(0, 5))
        self.btn_draw_rect = tk.Button(draw_btn_frame, text="[+] Prostokąt", command=self.toggle_draw_rect_mode, bg="#FFC107")
        self.btn_draw_rect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        self.btn_draw = tk.Button(draw_btn_frame, text="[+] Wielokąt", command=self.toggle_draw_mode, bg="#FFC107")
        self.btn_draw.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.approx_poly_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.tools_inner, text="Aproksymuj wielokąt do prostokąta", variable=self.approx_poly_var, bg="#f0f0f0", font=("Arial", 8)).pack(anchor=tk.W, pady=(0, 5))

        del_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        del_frame.pack(fill=tk.X, pady=(0, 5))
        self.btn_delete = tk.Button(del_frame, text="Usuń zazn. pole", command=self.delete_current_box, bg="#FF5252", fg="white")
        self.btn_delete.pack(side=tk.LEFT)
        self.chk_fast_del = tk.Checkbutton(del_frame, text="Włącz szybkie usuwanie (DEL)", variable=self.fast_delete_var, bg="#f0f0f0", font=("Arial", 8))
        self.chk_fast_del.pack(side=tk.LEFT, padx=5)

        tk.Label(self.tools_inner, text="Oryginał:", bg="#f0f0f0", fg="#555", font=("Arial", 8)).pack(anchor=tk.W)
        self.txt_original = tk.Text(self.tools_inner, height=2, font=("Arial", 10, "bold"), bg="#f0f0f0", relief=tk.FLAT)
        self.txt_original.pack(fill=tk.X, pady=(0, 5))
        self.txt_original.config(state=tk.DISABLED)

        t_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        t_frame.pack(fill=tk.X, pady=(0, 2))
        tk.Label(t_frame, text="Tekst zastępczy:", bg="#f0f0f0", fg="#555", font=("Arial", 8)).pack(side=tk.LEFT)
        tk.Button(t_frame, text="X₂", command=self.insert_sub, font=("Arial", 8), pady=0).pack(side=tk.RIGHT, padx=2)
        tk.Button(t_frame, text="X²", command=self.insert_sup, font=("Arial", 8), pady=0).pack(side=tk.RIGHT)

        self.text_new = tk.Text(self.tools_inner, font=("Arial", 11), height=4)
        self.text_new.pack(fill=tk.X, pady=(0, 5))
        self.text_new.bind("<KeyRelease>", self.on_key_release)

        fmt_frame = tk.LabelFrame(self.tools_inner, text="Ustawienia", bg="#f0f0f0", padx=5, pady=2)
        fmt_frame.pack(fill=tk.X, pady=2)

        tk.Label(fmt_frame, text="Font:", bg="#f0f0f0", font=("Arial", 8)).grid(row=0, column=0, sticky=tk.W)
        self.font_var = tk.StringVar(value="Arial - Regular")
        self.btn_font = ttk.Menubutton(fmt_frame, textvariable=self.font_var)
        self.btn_font.grid(row=0, column=1, sticky=tk.EW, columnspan=2)

        tk.Button(fmt_frame, text="[Wszędzie]", font=("Arial", 7), command=self.apply_font_everywhere).grid(row=0, column=3, padx=2)

        font_menu = tk.Menu(self.btn_font, tearoff=0)
        letters = sorted([k for k in GROUPED_FONTS.keys() if len(k) == 1])
        if "Pozostałe" in GROUPED_FONTS: letters.append("Pozostałe")
        for letter in letters:
            letter_menu = tk.Menu(font_menu, tearoff=0)
            families = GROUPED_FONTS[letter]
            for family in sorted(families.keys()):
                variants = families[family]
                if len(variants) == 1:
                    variant_name, unique_name = variants[0]
                    letter_menu.add_command(label=family, command=lambda f=unique_name: self.change_font(f))
                else:
                    family_menu = tk.Menu(letter_menu, tearoff=0)
                    for variant_name, unique_name in variants:
                        family_menu.add_command(label=variant_name, command=lambda f=unique_name: self.change_font(f))
                    letter_menu.add_cascade(label=family, menu=family_menu)
            font_menu.add_cascade(label=letter, menu=letter_menu)
        self.btn_font["menu"] = font_menu

        self.size_var = tk.StringVar()
        tk.Label(fmt_frame, text="Wielkość:", bg="#f0f0f0", font=("Arial", 8)).grid(row=1, column=0, sticky=tk.W)
        self.spin_size = ttk.Spinbox(fmt_frame, from_=8, to=100, width=4, textvariable=self.size_var)
        self.spin_size.grid(row=1, column=1, sticky=tk.W)
        self.size_var.trace_add("write", lambda *args: self.auto_apply())

        self.align_var = tk.StringVar()
        tk.Label(fmt_frame, text="Poziom:", bg="#f0f0f0", font=("Arial", 8)).grid(row=1, column=2, sticky=tk.W, padx=(5,0))
        self.combo_align = ttk.Combobox(fmt_frame, textvariable=self.align_var, values=["Lewo", "Środek", "Prawo"], state="readonly", width=7)
        self.combo_align.grid(row=1, column=3, sticky=tk.W)
        self.align_var.trace_add("write", lambda *args: self.auto_apply())

        self.angle_var = tk.StringVar()
        tk.Label(fmt_frame, text="Kąt (°):", bg="#f0f0f0", font=("Arial", 8)).grid(row=2, column=0, sticky=tk.W)
        self.spin_angle = ttk.Spinbox(fmt_frame, from_=-180, to=180, increment=1, width=4, textvariable=self.angle_var)
        self.spin_angle.grid(row=2, column=1, sticky=tk.W)
        self.angle_var.trace_add("write", lambda *args: self.auto_apply())

        self.valign_var = tk.StringVar()
        tk.Label(fmt_frame, text="Pion:", bg="#f0f0f0", font=("Arial", 8)).grid(row=2, column=2, sticky=tk.W, padx=(5,0))
        self.combo_valign = ttk.Combobox(fmt_frame, textvariable=self.valign_var, values=["Góra", "Środek", "Dół"], state="readonly", width=7)
        self.combo_valign.grid(row=2, column=3, sticky=tk.W)
        self.valign_var.trace_add("write", lambda *args: self.auto_apply())

        self.spacing_var = tk.StringVar()
        tk.Label(fmt_frame, text="Interlinia:", bg="#f0f0f0", font=("Arial", 8)).grid(row=3, column=0, sticky=tk.W)
        self.spin_spacing = ttk.Spinbox(fmt_frame, from_=-20, to=50, width=4, textvariable=self.spacing_var)
        self.spin_spacing.grid(row=3, column=1, sticky=tk.W)
        self.spacing_var.trace_add("write", lambda *args: self.auto_apply())

        dim_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        dim_frame.pack(fill=tk.X, pady=2)
        tk.Label(dim_frame, text="Wymiary pikselowe (W, H):", bg="#f0f0f0", font=("Arial", 8)).pack(side=tk.LEFT)
        self.height_var = tk.StringVar()
        self.spin_h = ttk.Spinbox(dim_frame, from_=1, to=5000, width=5, textvariable=self.height_var)
        self.spin_h.pack(side=tk.RIGHT, padx=2)
        self.width_var = tk.StringVar()
        self.spin_w = ttk.Spinbox(dim_frame, from_=1, to=5000, width=5, textvariable=self.width_var)
        self.spin_w.pack(side=tk.RIGHT)
        self.width_var.trace_add("write", lambda *args: self.auto_apply())
        self.height_var.trace_add("write", lambda *args: self.auto_apply())

        shift_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        shift_frame.pack(fill=tk.X, pady=2)
        tk.Label(shift_frame, text="Przesunięcie względem tła (X, Y):", bg="#f0f0f0", font=("Arial", 8)).pack(side=tk.LEFT)
        self.shift_y_var = tk.StringVar()
        self.spin_y = ttk.Spinbox(shift_frame, from_=-100, to=100, width=4, textvariable=self.shift_y_var)
        self.spin_y.pack(side=tk.RIGHT, padx=2)
        self.shift_x_var = tk.StringVar()
        self.spin_x = ttk.Spinbox(shift_frame, from_=-100, to=100, width=4, textvariable=self.shift_x_var)
        self.spin_x.pack(side=tk.RIGHT)
        self.shift_x_var.trace_add("write", lambda *args: self.auto_apply())
        self.shift_y_var.trace_add("write", lambda *args: self.auto_apply())

        btn_frame = tk.Frame(self.tools_inner, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, pady=5)
        tk.Button(btn_frame, text="Ignoruj (Bez zmian)", command=self.ignore_box).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(btn_frame, text="Przywróć (OCR)", command=self.revert_to_original).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.bottom_pane = tk.Frame(self.paned_window, bg="#f0f0f0")
        self.paned_window.add(self.bottom_pane, minsize=100, stretch="always")

        zoom_frame = tk.Frame(self.bottom_pane, bg="#e0e0e0", bd=1, relief=tk.SUNKEN)
        zoom_frame.pack(side=tk.TOP, fill=tk.X)
        tk.Label(zoom_frame, text=" Zoom Galerii:", bg="#e0e0e0", font=("Arial", 8, "bold")).pack(side=tk.LEFT)
        self.zoom_slider = ttk.Scale(zoom_frame, from_=50, to=280, orient=tk.HORIZONTAL)
        self.zoom_slider.set(self.thumbnail_size)
        self.zoom_slider.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)
        self.zoom_slider.bind("<ButtonRelease-1>", self.apply_zoom)

        self.gal_canvas = tk.Canvas(self.bottom_pane, bg="#f0f0f0", highlightthickness=0)
        self.gal_scrollbar = ttk.Scrollbar(self.bottom_pane, orient="vertical", command=self.gal_canvas.yview)
        self.gal_inner = tk.Frame(self.gal_canvas, bg="#f0f0f0")

        self.gal_inner.bind("<Configure>", lambda e: self.gal_canvas.configure(scrollregion=self.gal_canvas.bbox("all")))
        self.gal_canvas.create_window((0, 0), window=self.gal_inner, anchor="nw")
        self.gal_canvas.configure(yscrollcommand=self.gal_scrollbar.set)

        self.gal_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.gal_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.gal_canvas.bind_all("<MouseWheel>", self._on_mousewheel_gallery)

        self.render_gallery()

        self.canvas.bind("<Configure>", self.redraw_canvas)
        self.canvas.bind("<ButtonPress-1>", self.on_left_click)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<ButtonPress-3>", self.on_right_click)

    def on_key_release(self, event):
        if self._typing_timer:
            self.frame.after_cancel(self._typing_timer)
        self._typing_timer = self.frame.after(250, self.auto_apply)

    def _set_selected_box(self, box):
        changed = (self.selected_box != box)
        self.selected_box = box
        if hasattr(self, 'processor') and self.processor:
            self.processor.selected_box_id = box["id"] if box else None
            if changed and self.alpha_var.get() < 100.0:
                self.processor.image_changed = True

    def on_alpha_slide(self, event=None):
        if hasattr(self, 'processor') and self.processor:
            self.processor.selected_alpha = self.alpha_var.get() / 100.0
            if self.selected_box:
                self.processor.image_changed = True
                self.redraw_canvas()

    def toggle_text_preview(self):
        if hasattr(self, 'processor') and self.processor:
            self.processor.show_replacement_text = self.show_text_var.get()
            self.processor.image_changed = True
            self.redraw_canvas()

    def handle_v_key(self, event):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)): return
        self.toggle_format_painter()

    def handle_c_key(self, event):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)): return
        self.toggle_size_painter()

    def handle_x_key(self, event):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)): return
        self.toggle_aio_painter()

    def apply_font_everywhere(self):
        if not self.selected_box: return
        font = self.selected_box["font_family"]
        for box in self.processor.boxes:
            box["font_family"] = font
        self.processor.image_changed = True
        self._save_global_state()
        self.redraw_canvas()

    def clear_painters(self):
        self.format_source_box = None
        self.size_source_box = None
        self.aio_source_box = None
        self.btn_format_painter.config(bg="#e0e0e0")
        self.btn_size_painter.config(bg="#e0e0e0")
        self.btn_aio_painter.config(bg="#e0e0e0")
        self.canvas.config(cursor="cross")

    def toggle_format_painter(self):
        if not self.selected_box: return
        if getattr(self, 'format_source_box', None):
            self.clear_painters()
        else:
            self.clear_painters()
            self.format_source_box = copy.deepcopy(self.selected_box)
            self.btn_format_painter.config(bg="#4CAF50")
            self.canvas.config(cursor="hand2")

    def toggle_size_painter(self):
        if not self.selected_box: return
        if getattr(self, 'size_source_box', None):
            self.clear_painters()
        else:
            self.clear_painters()
            self.size_source_box = copy.deepcopy(self.selected_box)
            self.btn_size_painter.config(bg="#4CAF50")
            self.canvas.config(cursor="hand2")

    def toggle_aio_painter(self):
        if not self.selected_box: return
        if getattr(self, 'aio_source_box', None):
            self.clear_painters()
        else:
            self.clear_painters()
            self.aio_source_box = copy.deepcopy(self.selected_box)
            self.btn_aio_painter.config(bg="#4CAF50")
            self.canvas.config(cursor="hand2")

    def apply_size_paste(self, target_box, source_box):
        src_pts = source_box["points"]
        src_w = max(p[0] for p in src_pts) - min(p[0] for p in src_pts)
        src_h = max(p[1] for p in src_pts) - min(p[1] for p in src_pts)

        tgt_pts = target_box["points"]
        tgt_xs = [p[0] for p in tgt_pts]
        tgt_ys = [p[1] for p in tgt_pts]
        tgt_w = max(tgt_xs) - min(tgt_xs)
        tgt_h = max(tgt_ys) - min(tgt_ys)

        cx = sum(tgt_xs) / len(tgt_xs)
        cy = sum(tgt_ys) / len(tgt_ys)

        sx = src_w / tgt_w if tgt_w > 0 else 1
        sy = src_h / tgt_h if tgt_h > 0 else 1

        new_pts = [(int(round(cx + (p[0]-cx)*sx)), int(round(cy + (p[1]-cy)*sy))) for p in tgt_pts]
        if is_valid_poly(new_pts):
            target_box["points"] = new_pts

    def copy_box(self, event=None):
        if self.selected_box:
            self.clipboard_box = copy.deepcopy(self.selected_box)

    def paste_box(self, event=None):
        if hasattr(self, 'clipboard_box') and self.clipboard_box:
            new_box = copy.deepcopy(self.clipboard_box)
            new_box["id"] = str(uuid.uuid4())
            new_box["points"] = [(p[0]+30, p[1]+30) for p in new_box["points"]]
            self.processor.boxes.append(new_box)
            self._set_selected_box(new_box)
            self.processor.image_changed = True
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()

    def handle_delete_key(self, event):
        focused = self.frame.focus_get()
        if isinstance(focused, (tk.Text, tk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)):
            return

        if self.fast_delete_var.get() and self.selected_box:
            self.delete_current_box()

    def reset_zoom(self):
        self.canvas_zoom_var.set(100.0)
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        self.redraw_canvas()

    def _on_mousewheel_canvas(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        new_val = self.canvas_zoom_var.get() * factor
        new_val = max(20, min(new_val, 500))
        self.canvas_zoom_var.set(new_val)
        self.redraw_canvas()

    def _on_mousewheel_tools(self, event):
        if self.tools_canvas.winfo_rootx() <= event.widget.winfo_rootx() <= self.tools_canvas.winfo_rootx() + self.tools_canvas.winfo_width():
            self.tools_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def _on_mousewheel_gallery(self, event):
        if self.gal_canvas.winfo_rootx() <= event.widget.winfo_rootx() <= self.gal_canvas.winfo_rootx() + self.gal_canvas.winfo_width():
            self.gal_canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def apply_zoom(self, event=None):
        self.thumbnail_size = int(self.zoom_slider.get())
        self.render_gallery()

    def render_gallery(self):
        for widget in self.gal_inner.winfo_children(): widget.destroy()

        self.gallery_widgets = []
        self.thumbnails = []

        size = self.thumbnail_size
        cols = max(1, 280 // (size + 20))

        for idx, pil_img in enumerate(self.source_thumbnails):
            img_copy = pil_img.copy()
            img_copy.thumbnail((size, size), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
            photo_img = ImageTk.PhotoImage(img_copy)
            self.thumbnails.append(photo_img)

            item_frame = tk.Frame(self.gal_inner, bg="#f0f0f0", bd=2, relief=tk.FLAT, pady=2, padx=2)
            row = idx // cols
            col = idx % cols
            item_frame.grid(row=row, column=col, padx=4, pady=4, sticky=tk.N)

            lbl_img = tk.Label(item_frame, image=photo_img, bg="#f0f0f0", cursor="hand2")
            lbl_img.pack()

            filename = self.image_files[idx]
            short_name = filename if len(filename) <= 15 else filename[:6] + "..." + filename[-5:]
            lbl_txt = tk.Label(item_frame, text=short_name, bg="#f0f0f0", font=("Arial", 8))
            lbl_txt.pack()

            for w in (item_frame, lbl_img, lbl_txt):
                w.bind("<Button-1>", lambda e, i=idx: self.user_clicked_gallery(i))

            self.gallery_widgets.append((item_frame, lbl_img, lbl_txt))

        self.update_gallery_highlight(auto_scroll=False)

    def user_clicked_gallery(self, index):
        self.select_image(index, auto_scroll=False)

    def update_gallery_highlight(self, auto_scroll=True):
        target_frame = None
        for idx, (frame, lbl_img, lbl_txt) in enumerate(self.gallery_widgets):
            if idx == self.current_index:
                frame.config(bg="#90CAF9", relief=tk.RAISED)
                lbl_txt.config(bg="#90CAF9", font=("Arial", 8, "bold"))
                lbl_img.config(bg="#90CAF9")
                target_frame = frame
            else:
                frame.config(bg="#f0f0f0", relief=tk.FLAT)
                lbl_txt.config(bg="#f0f0f0", font=("Arial", 8, "normal"))
                lbl_img.config(bg="#f0f0f0")

        if auto_scroll and target_frame:
            self.gal_canvas.update_idletasks()
            y_pos = target_frame.winfo_y()
            canvas_h = self.gal_canvas.winfo_height()
            total_h = self.gal_inner.winfo_height()
            if total_h > 0:
                fraction = y_pos / total_h
                fraction -= (canvas_h / 2) / total_h
                self.gal_canvas.yview_moveto(max(0, fraction))

    def select_image(self, index, auto_scroll=True):
        if index < 0 or index >= len(self.image_files): return

        self.current_index = index
        filename = self.image_files[self.current_index]
        self.frame.master.title(f"Tłumacz OCR - {filename} ({self.current_index + 1}/{len(self.image_files)})")

        self.processor = self.processors[filename]
        self._set_selected_box(None)

        if self.processor:
            self.processor.selected_alpha = self.alpha_var.get() / 100.0

        if getattr(self, 'drawing_poly_mode', False): self.toggle_draw_mode()
        if getattr(self, 'drawing_rect_mode', False): self.toggle_draw_rect_mode()

        self.canvas_zoom_var.set(100.0)
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        self.update_sidebar()
        self.update_gallery_highlight(auto_scroll)
        self.redraw_canvas()

    def prev_image(self): self.select_image(self.current_index - 1, auto_scroll=True)
    def next_image(self): self.select_image(self.current_index + 1, auto_scroll=True)

    def export_current(self):
        filename = self.image_files[self.current_index]
        default_name = os.path.basename(filename)
        out_path = filedialog.asksaveasfilename(
            title="Zapisz bieżący obraz",
            initialfile=default_name,
            defaultextension=os.path.splitext(default_name)[1]
        )
        if out_path:
            self.processor.save(out_path)
            messagebox.showinfo("Zapisano", f"Pomyślnie wyeksportowano bieżący plik:\n{out_path}")

    def export_all(self):
        if self.original_zip_path:
            save_path = filedialog.asksaveasfilename(
                title="Zapisz nowy plik ZIP",
                defaultextension=".zip",
                filetypes=[("Pliki ZIP", "*.zip")],
                initialfile="przetlumaczone_" + os.path.basename(self.original_zip_path)
            )
            if not save_path: return

            with zipfile.ZipFile(self.original_zip_path, 'r') as zin:
                with zipfile.ZipFile(save_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
                    rel_paths_fwd = {p.replace('\\', '/'): p for p in self.processors.keys()}
                    for item in zin.infolist():
                        zip_path = item.filename
                        if zip_path.endswith('/'): continue
                        if zip_path in rel_paths_fwd:
                            rel_p = rel_paths_fwd[zip_path]
                            proc = self.processors[rel_p]
                            proc.apply_all_edits()
                            ext = os.path.splitext(rel_p)[1]
                            is_success, im_buf_arr = cv2.imencode(ext, proc.current_cv_image)
                            if is_success:
                                zout.writestr(item, im_buf_arr.tobytes())
                            else:
                                zout.writestr(item, zin.read(item.filename))
                        else:
                            zout.writestr(item, zin.read(item.filename))
            messagebox.showinfo("Zapisano", f"Pomyślnie utworzono nowy ZIP:\n{save_path}")
        else:
            out_dir = filedialog.askdirectory(title="Wybierz folder docelowy dla wszystkich zdjęć")
            if not out_dir: return

            apply_to_all_choice = None
            saved_count = 0

            for filename, proc in self.processors.items():
                dest_path = os.path.join(out_dir, filename)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                if os.path.exists(dest_path):
                    if apply_to_all_choice:
                        choice = apply_to_all_choice
                    else:
                        dialog = ConflictDialog(self.frame.winfo_toplevel(), os.path.basename(filename))
                        self.frame.wait_window(dialog)
                        choice = dialog.choice
                        if dialog.apply_to_all:
                            apply_to_all_choice = choice

                    if choice == "skip":
                        continue
                    elif choice == "keep":
                        base, ext = os.path.splitext(dest_path)
                        counter = 1
                        while os.path.exists(f"{base} ({counter}){ext}"):
                            counter += 1
                        dest_path = f"{base} ({counter}){ext}"

                proc.save(dest_path)
                saved_count += 1

            messagebox.showinfo("Zapisano", f"Eksport zakończony!\nZapisano {saved_count} plików do:\n{out_dir}")

    def change_font(self, unique_font_name):
        if self._is_updating_sidebar: return
        self.font_var.set(unique_font_name)
        self.auto_apply()

    def insert_sub(self): self._insert_tag("_{", "}")
    def insert_sup(self): self._insert_tag("^{", "}")
    def _insert_tag(self, open_tag, close_tag):
        try:
            sel_first = self.text_new.index(tk.SEL_FIRST)
            sel_last = self.text_new.index(tk.SEL_LAST)
            text = self.text_new.get(sel_first, sel_last)
            self.text_new.delete(sel_first, sel_last)
            self.text_new.insert(sel_first, f"{open_tag}{text}{close_tag}")
        except tk.TclError:
            self.text_new.insert(tk.INSERT, f"{open_tag}{close_tag}")
            self.text_new.mark_set(tk.INSERT, f"{tk.INSERT}-1c")
        self.auto_apply()

    def toggle_draw_rect_mode(self):
        self.drawing_rect_mode = not getattr(self, 'drawing_rect_mode', False)
        if self.drawing_rect_mode:
            if getattr(self, 'drawing_poly_mode', False): self.toggle_draw_mode()
            self.btn_draw_rect.config(bg="#4CAF50", text="[Trwa rysowanie...]")
            self.canvas.config(cursor="crosshair")
            self._set_selected_box(None)
            self.update_sidebar()
            self.redraw_canvas()
        else:
            self.btn_draw_rect.config(bg="#FFC107", text="[+] Prostokąt")
            self.canvas.config(cursor="cross")
            self.canvas.delete("temp_rect")
            self.redraw_canvas()

    def toggle_draw_mode(self):
        self.drawing_poly_mode = not getattr(self, 'drawing_poly_mode', False)
        self.current_poly_points = []
        for line in getattr(self, 'temp_lines', []):
            self.canvas.delete(line)
        self.temp_lines = []

        if self.drawing_poly_mode:
            if getattr(self, 'drawing_rect_mode', False): self.toggle_draw_rect_mode()
            self.btn_draw.config(bg="#4CAF50", text="[Trwa rysowanie...]")
            self.canvas.config(cursor="crosshair")
            self._set_selected_box(None)
            self.update_sidebar()
            self.redraw_canvas()
        else:
            self.btn_draw.config(bg="#FFC107", text="[+] Wielokąt")
            self.canvas.config(cursor="cross")
            self.redraw_canvas()

    def redraw_canvas(self, event=None):
        if not hasattr(self, 'processor'): return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10: return

        needs_pixel_update = self.processor.image_changed

        img_rgb = self.processor.get_rgb_image()
        img_h, img_w = img_rgb.shape[:2]

        self.base_scale = min(canvas_w / img_w, canvas_h / img_h)
        if self.base_scale > 1.5: self.base_scale = 1.5

        zoom_multiplier = self.canvas_zoom_var.get() / 100.0
        new_scale = self.base_scale * zoom_multiplier

        if getattr(self, '_last_scale', None) != new_scale or needs_pixel_update or not hasattr(self, 'tk_img'):
            self.scale = new_scale
            new_w, new_h = int(img_w * self.scale), int(img_h * self.scale)
            if new_w > 10000 or new_h > 10000: return

            resized_cv = cv2.resize(img_rgb, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            self.tk_img = ImageTk.PhotoImage(image=Image.fromarray(resized_cv))

            self._last_scale = self.scale
            self._last_w, self._last_h = new_w, new_h

        base_off_x = (canvas_w - self._last_w) // 2
        base_off_y = (canvas_h - self._last_h) // 2
        self.offset_x = base_off_x + self.pan_offset_x
        self.offset_y = base_off_y + self.pan_offset_y

        self.canvas.delete("all")
        self.canvas.create_image(self.offset_x, self.offset_y, anchor=tk.NW, image=self.tk_img)

        show_boxes = self.show_boxes_var.get()

        if show_boxes:
            for box in self.processor.boxes:
                color = "#00FF00" if box == self.selected_box else ("gray" if box["ignored"] else ("#FFA500" if box["new_text"] is not None else "red"))
                scaled_pts = [(int(p[0]*self.scale) + self.offset_x, int(p[1]*self.scale) + self.offset_y) for p in box["points"]]

                flat_pts = [coord for pt in scaled_pts for coord in pt]
                if len(scaled_pts) > 2:
                    self.canvas.create_polygon(flat_pts, outline=color, fill="", width=2)
                elif len(scaled_pts) == 2:
                    self.canvas.create_line(flat_pts, fill=color, width=2)

            if self.selected_box:
                pts = self.selected_box["points"]
                N = len(pts)
                for i in range(N):
                    px, py = int(pts[i][0]*self.scale) + self.offset_x, int(pts[i][1]*self.scale) + self.offset_y
                    self.canvas.create_rectangle(px-4, py-4, px+4, py+4, fill="#2196F3", outline="white", width=1)
                    p_next = pts[(i+1)%N]
                    mx, my = (pts[i][0] + p_next[0]) / 2.0, (pts[i][1] + p_next[1]) / 2.0
                    mpx, mpy = int(mx*self.scale) + self.offset_x, int(my*self.scale) + self.offset_y
                    self.canvas.create_oval(mpx-4, mpy-4, mpx+4, mpy+4, fill="#FF9800", outline="white", width=1)

        if getattr(self, 'drawing_poly_mode', False) and len(self.current_poly_points) > 0:
            for i in range(len(self.current_poly_points) - 1):
                p1, p2 = self.current_poly_points[i], self.current_poly_points[i+1]
                x1, y1 = p1[0]*self.scale + self.offset_x, p1[1]*self.scale + self.offset_y
                x2, y2 = p2[0]*self.scale + self.offset_x, p2[1]*self.scale + self.offset_y
                self.canvas.create_line(x1, y1, x2, y2, fill="magenta", width=2)

    def get_orig_coords(self, event_x, event_y):
        return (event_x - self.offset_x) / self.scale, (event_y - self.offset_y) / self.scale

    def point_in_polygon(self, pt, poly):
        x, y = pt
        n = len(poly)
        inside = False
        if n == 0: return False
        p1x, p1y = poly[0]
        for i in range(n + 1):
            p2x, p2y = poly[i % n]
            if y > min(p1y, p2y) and y <= max(p1y, p2y) and x <= max(p1x, p2x):
                if p1y != p2y: xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if p1x == p2x or x <= xints: inside = not inside
            p1x, p1y = p2x, p2y
        return inside

    def on_left_click(self, event):
        orig_x, orig_y = self.get_orig_coords(event.x, event.y)

        if getattr(self, 'drawing_rect_mode', False):
            self.rect_start_x = orig_x
            self.rect_start_y = orig_y
            return

        if getattr(self, 'drawing_poly_mode', False):
            self.current_poly_points.append((orig_x, orig_y))
            self.redraw_canvas()
            return

        clicked_box = None
        for box in reversed(self.processor.boxes):
            if self.point_in_polygon((orig_x, orig_y), box["points"]):
                clicked_box = box; break

        if getattr(self, 'format_source_box', None) and clicked_box:
            clicked_box["font_family"] = self.format_source_box["font_family"]
            clicked_box["font_size"] = self.format_source_box["font_size"]
            clicked_box["alignment"] = self.format_source_box["alignment"]
            clicked_box["valign"] = self.format_source_box.get("valign", "Środek")
            clicked_box["angle"] = self.format_source_box.get("angle", 0.0)
            clicked_box["line_spacing"] = self.format_source_box.get("line_spacing", 2)
            self.clear_painters()
            self.processor.image_changed = True
            self._set_selected_box(clicked_box)
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()
            return

        if getattr(self, 'size_source_box', None) and clicked_box:
            self.apply_size_paste(clicked_box, self.size_source_box)
            self.clear_painters()
            self.processor.image_changed = True
            self._set_selected_box(clicked_box)
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()
            return

        if getattr(self, 'aio_source_box', None) and clicked_box:
            clicked_box["font_family"] = self.aio_source_box["font_family"]
            clicked_box["font_size"] = self.aio_source_box["font_size"]
            clicked_box["alignment"] = self.aio_source_box["alignment"]
            clicked_box["valign"] = self.aio_source_box.get("valign", "Środek")
            clicked_box["angle"] = self.aio_source_box.get("angle", 0.0)
            clicked_box["line_spacing"] = self.aio_source_box.get("line_spacing", 2)
            self.apply_size_paste(clicked_box, self.aio_source_box)
            self.clear_painters()
            self.processor.image_changed = True
            self._set_selected_box(clicked_box)
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()
            return

        if self.selected_box:
            pts = self.selected_box["points"]
            N = len(pts)
            for i, pt in enumerate(pts):
                dist = math.hypot(pt[0] - orig_x, pt[1] - orig_y)
                if dist < 15 / self.scale:
                    self.dragged_vertex_idx = i
                    self.drag_start_x = orig_x
                    self.drag_start_y = orig_y
                    self.drag_start_points = copy.deepcopy(pts)
                    return
            for i in range(N):
                p1, p2 = pts[i], pts[(i+1)%N]
                mx, my = (p1[0]+p2[0])/2.0, (p1[1]+p2[1])/2.0
                dist = math.hypot(mx - orig_x, my - orig_y)
                if dist < 15 / self.scale:
                    self.dragged_edge_idx = i
                    self.drag_start_x = orig_x
                    self.drag_start_y = orig_y
                    self.drag_start_points = copy.deepcopy(pts)
                    return

        if clicked_box:
            self._set_selected_box(clicked_box)
            self.update_sidebar()
            self.canvas.focus_set()
            self.dragged_box = True
            self.drag_start_x = orig_x
            self.drag_start_y = orig_y
            self.drag_start_points = copy.deepcopy(self.selected_box["points"])
            self.redraw_canvas()
        else:
            self._set_selected_box(None)
            self.update_sidebar()
            self.redraw_canvas()

        if not clicked_box and self.dragged_vertex_idx is None and getattr(self, 'dragged_edge_idx', None) is None:
            self.is_panning = True
            self.pan_last_x = event.x
            self.pan_last_y = event.y
            self.canvas.config(cursor="fleur")

    def on_mouse_drag(self, event):
        if getattr(self, 'drawing_rect_mode', False) and hasattr(self, 'rect_start_x'):
            self.canvas.delete("temp_rect")
            px1, py1 = self.rect_start_x * self.scale + self.offset_x, self.rect_start_y * self.scale + self.offset_y
            px2, py2 = event.x, event.y
            self.canvas.create_rectangle(px1, py1, px2, py2, outline="magenta", width=2, tags="temp_rect")
            return

        if self.dragged_vertex_idx is not None and self.selected_box:
            orig_x, orig_y = self.get_orig_coords(event.x, event.y)
            dx = orig_x - self.drag_start_x
            dy = orig_y - self.drag_start_y
            if event.state & 0x0001:
                if abs(dx) > abs(dy): dy = 0
                else: dx = 0

            new_pts = copy.deepcopy(self.drag_start_points)
            new_pts[self.dragged_vertex_idx] = (int(self.drag_start_points[self.dragged_vertex_idx][0] + dx),
                                                int(self.drag_start_points[self.dragged_vertex_idx][1] + dy))
            if is_valid_poly(new_pts):
                self.selected_box["points"] = new_pts
                self._update_size_spinners_silently(new_pts)
                self.redraw_canvas()

        elif getattr(self, 'dragged_edge_idx', None) is not None and self.selected_box:
            orig_x, orig_y = self.get_orig_coords(event.x, event.y)
            dx = orig_x - self.drag_start_x
            dy = orig_y - self.drag_start_y
            if event.state & 0x0001:
                if abs(dx) > abs(dy): dy = 0
                else: dx = 0

            P = self.drag_start_points
            N = len(P)
            i = self.dragged_edge_idx

            E1 = P[i]
            E2 = P[(i+1)%N]
            T1 = (E1[0]+dx, E1[1]+dy)
            V_T = (E2[0]-E1[0], E2[1]-E1[1])
            R1 = P[(i-1)%N]
            V_R1 = (E1[0]-R1[0], E1[1]-R1[1])
            R2 = P[(i+2)%N]
            V_R2 = (E2[0]-R2[0], E2[1]-R2[1])

            new_E1 = get_line_intersection(R1, V_R1, T1, V_T)
            new_E2 = get_line_intersection(R2, V_R2, T1, V_T)
            if new_E1 is None: new_E1 = (E1[0]+dx, E1[1]+dy)
            if new_E2 is None: new_E2 = (E2[0]+dx, E2[1]+dy)

            new_pts = copy.deepcopy(P)
            new_pts[i] = (int(round(new_E1[0])), int(round(new_E1[1])))
            new_pts[(i+1)%N] = (int(round(new_E2[0])), int(round(new_E2[1])))

            if is_valid_poly(new_pts):
                self.selected_box["points"] = new_pts
                self._update_size_spinners_silently(new_pts)
                self.redraw_canvas()

        elif getattr(self, 'dragged_box', False) and self.selected_box:
            orig_x, orig_y = self.get_orig_coords(event.x, event.y)
            dx = orig_x - self.drag_start_x
            dy = orig_y - self.drag_start_y

            if event.state & 0x0001:
                if abs(dx) > abs(dy): dy = 0
                else: dx = 0

            temp_points = [(p[0]+dx, p[1]+dy) for p in self.drag_start_points]

            self.canvas.delete("snap_line")
            if event.state & 0x0001:
                xs = [p[0] for p in temp_points]
                ys = [p[1] for p in temp_points]
                t_minx, t_maxx, t_cx = min(xs), max(xs), sum(xs)/len(xs)
                t_miny, t_maxy, t_cy = min(ys), max(ys), sum(ys)/len(ys)

                snap_threshold = 10 / self.scale
                best_dx, best_dy = 0, 0
                min_diff_x, min_diff_y = float('inf'), float('inf')
                snap_lines_x, snap_lines_y = [], []

                for box in self.processor.boxes:
                    if box["id"] == self.selected_box["id"]: continue
                    b_xs = [p[0] for p in box["points"]]
                    b_ys = [p[1] for p in box["points"]]
                    b_minx, b_maxx, b_cx = min(b_xs), max(b_xs), sum(b_xs)/len(b_xs)
                    b_miny, b_maxy, b_cy = min(b_ys), max(b_ys), sum(b_ys)/len(b_ys)

                    if dy == 0:
                        for target_x in [b_minx, b_maxx, b_cx]:
                            for source_x in [t_minx, t_maxx, t_cx]:
                                diff = target_x - source_x
                                if abs(diff) < snap_threshold and abs(diff) < abs(min_diff_x):
                                    min_diff_x = diff
                                    best_dx = diff
                                    snap_lines_x = [target_x]

                    if dx == 0:
                        for target_y in [b_miny, b_maxy, b_cy]:
                            for source_y in [t_miny, t_maxy, t_cy]:
                                diff = target_y - source_y
                                if abs(diff) < snap_threshold and abs(diff) < abs(min_diff_y):
                                    min_diff_y = diff
                                    best_dy = diff
                                    snap_lines_y = [target_y]

                if min_diff_x != float('inf'):
                    temp_points = [(p[0]+best_dx, p[1]) for p in temp_points]
                    for x in snap_lines_x:
                        px = x * self.scale + self.offset_x
                        self.canvas.create_line(px, 0, px, self.canvas.winfo_height(), fill="cyan", dash=(4,4), tags="snap_line")
                if min_diff_y != float('inf'):
                    temp_points = [(p[0], p[1]+best_dy) for p in temp_points]
                    for y in snap_lines_y:
                        py = y * self.scale + self.offset_y
                        self.canvas.create_line(0, py, self.canvas.winfo_width(), py, fill="cyan", dash=(4,4), tags="snap_line")

            if is_valid_poly(temp_points):
                self.selected_box["points"] = temp_points
                self._update_size_spinners_silently(temp_points)
                self.redraw_canvas()

        elif getattr(self, 'is_panning', False):
            dx = event.x - self.pan_last_x
            dy = event.y - self.pan_last_y
            self.pan_offset_x += dx
            self.pan_offset_y += dy
            self.pan_last_x = event.x
            self.pan_last_y = event.y
            self.redraw_canvas()

    def _update_size_spinners_silently(self, pts):
        self._is_updating_sidebar = True
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.width_var.set(str(int(max(xs) - min(xs))))
        self.height_var.set(str(int(max(ys) - min(ys))))
        self._is_updating_sidebar = False

    def on_left_release(self, event):
        self.canvas.delete("snap_line")
        if getattr(self, 'drawing_rect_mode', False) and hasattr(self, 'rect_start_x'):
            orig_x, orig_y = self.get_orig_coords(event.x, event.y)
            if abs(orig_x - self.rect_start_x) > 5 and abs(orig_y - self.rect_start_y) > 5:
                new_box = self.processor.add_manual_rectangle(self.rect_start_x, self.rect_start_y, orig_x, orig_y)
                new_box["font_family"] = FONT_MAP.get(self.font_var.get(), "arial.ttf")
                new_box["font_size"] = self.last_font_size
                new_box["line_spacing"] = self.last_line_spacing
                new_box["alignment"] = "Środek"
                new_box["valign"] = "Środek"
                self._set_selected_box(new_box)

            self.canvas.delete("temp_rect")
            self.toggle_draw_rect_mode()
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()
            return

        if self.dragged_vertex_idx is not None:
            self.dragged_vertex_idx = None
            self.processor.image_changed = True
            self._save_global_state()
            self.redraw_canvas()

        if getattr(self, 'dragged_edge_idx', None) is not None:
            self.dragged_edge_idx = None
            self.processor.image_changed = True
            self._save_global_state()
            self.redraw_canvas()

        if getattr(self, 'dragged_box', False):
            self.dragged_box = False
            self.processor.image_changed = True
            self._save_global_state()
            self.redraw_canvas()

        if getattr(self, 'is_panning', False):
            self.is_panning = False
            self.canvas.config(cursor="cross")

    def on_right_click(self, event):
        if getattr(self, 'drawing_poly_mode', False) and len(self.current_poly_points) > 2:
            approx = self.approx_poly_var.get()
            new_box = self.processor.add_manual_polygon(self.current_poly_points, approximate=approx)

            new_box["font_family"] = FONT_MAP.get(self.font_var.get(), "arial.ttf")
            new_box["font_size"] = self.last_font_size
            new_box["line_spacing"] = self.last_line_spacing
            new_box["alignment"] = "Środek"
            new_box["valign"] = "Środek"

            self.processor.image_changed = True
            self._set_selected_box(new_box)
            self.toggle_draw_mode()
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()
            self.canvas.update()

    def delete_current_box(self):
        if self.selected_box:
            self.processor.delete_box(self.selected_box["id"])
            self._set_selected_box(None)
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()

    def update_sidebar(self):
        self._is_updating_sidebar = True
        self.text_new.delete("1.0", tk.END)
        self.size_var.set("")
        self.angle_var.set("0")
        self.spacing_var.set("")
        self.shift_x_var.set("0")
        self.shift_y_var.set("0")
        self.width_var.set("0")
        self.height_var.set("0")

        widgets_to_toggle = [self.btn_font, self.spin_size, self.combo_align, self.combo_valign,
                             self.spin_angle, self.spin_spacing, self.spin_x, self.spin_y, self.spin_w, self.spin_h]

        if self.selected_box:
            self.btn_delete.config(state="normal")
            orig = self.selected_box["original_text"]
            trans = self.selected_box.get("translated_text")

            self.txt_original.config(state=tk.NORMAL)
            self.txt_original.delete("1.0", tk.END)
            self.txt_original.insert("1.0", f"{orig}\n-> {trans}" if trans else orig)
            self.txt_original.config(state=tk.DISABLED)

            if self.selected_box["new_text"] is not None:
                self.text_new.insert("1.0", self.selected_box["new_text"])

            font_name = "Arial - Regular"
            for unique_name, filepath in FONT_MAP.items():
                if filepath == self.selected_box["font_family"]:
                    font_name = unique_name
                    break

            self.font_var.set(font_name)
            self.size_var.set(str(self.selected_box["font_size"]))
            self.align_var.set(self.selected_box["alignment"])
            self.valign_var.set(self.selected_box.get("valign", "Środek"))
            self.angle_var.set(str(int(self.selected_box.get("angle", 0))))
            self.spacing_var.set(str(self.selected_box.get("line_spacing", 2)))
            self.shift_x_var.set(str(self.selected_box.get("shift_x", 0)))
            self.shift_y_var.set(str(self.selected_box.get("shift_y", 0)))

            pts = self.selected_box["points"]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self.width_var.set(str(int(max(xs) - min(xs))))
            self.height_var.set(str(int(max(ys) - min(ys))))

            for w in widgets_to_toggle:
                state_mode = "readonly" if isinstance(w, ttk.Combobox) else "normal"
                w.config(state=state_mode)

        else:
            self.btn_delete.config(state="disabled")
            self.txt_original.config(state=tk.NORMAL)
            self.txt_original.delete("1.0", tk.END)
            self.txt_original.insert("1.0", "[Wybierz ramkę na obrazie]")
            self.txt_original.config(state=tk.DISABLED)

            for w in widgets_to_toggle:
                w.config(state="disabled")

        self._is_updating_sidebar = False

    def auto_apply(self, event=None):
        needs_pixel_update = False
        if getattr(self, "_is_updating_sidebar", False): return

        if self.selected_box:
            raw_text = self.text_new.get("1.0", tk.END)
            if raw_text.endswith('\n'): raw_text = raw_text[:-1]

            if self.selected_box["new_text"] != raw_text:
                self.selected_box["new_text"] = raw_text
                needs_pixel_update = True

            font = FONT_MAP.get(self.font_var.get(), "arial.ttf")
            if self.selected_box["font_family"] != font:
                self.selected_box["font_family"] = font
                needs_pixel_update = True

            try:
                v = int(self.size_var.get())
                if self.selected_box["font_size"] != v:
                    self.selected_box["font_size"] = v
                    self.last_font_size = v
                    needs_pixel_update = True
            except ValueError: pass

            try:
                v = float(self.angle_var.get())
                if self.selected_box.get("angle", 0) != v:
                    self.selected_box["angle"] = v
                    needs_pixel_update = True
            except ValueError: pass

            try:
                v = int(self.spacing_var.get())
                if self.selected_box.get("line_spacing", 2) != v:
                    self.selected_box["line_spacing"] = v
                    self.last_line_spacing = v
                    needs_pixel_update = True
            except ValueError: pass

            try:
                v = int(self.shift_x_var.get())
                if self.selected_box.get("shift_x", 0) != v:
                    self.selected_box["shift_x"] = v
                    needs_pixel_update = True
            except ValueError: pass

            try:
                v = int(self.shift_y_var.get())
                if self.selected_box.get("shift_y", 0) != v:
                    self.selected_box["shift_y"] = v
                    needs_pixel_update = True
            except ValueError: pass

            try:
                new_w = int(self.width_var.get())
                new_h = int(self.height_var.get())
                if new_w < 3: new_w = 3
                if new_h < 3: new_h = 3

                pts = self.selected_box["points"]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                cur_w = max(xs) - min(xs)
                cur_h = max(ys) - min(ys)

                if (new_w != int(cur_w) and new_w > 0) or (new_h != int(cur_h) and new_h > 0):
                    cx = sum(xs) / len(xs)
                    cy = sum(ys) / len(ys)
                    sx = new_w / cur_w if cur_w > 0 else 1
                    sy = new_h / cur_h if cur_h > 0 else 1
                    new_pts = [(int(round(cx + (p[0]-cx)*sx)), int(round(cy + (p[1]-cy)*sy))) for p in pts]
                    if is_valid_poly(new_pts):
                        self.selected_box["points"] = new_pts
                        needs_pixel_update = True
            except ValueError: pass

            if self.selected_box["alignment"] != self.align_var.get():
                self.selected_box["alignment"] = self.align_var.get()
                needs_pixel_update = True

            if self.selected_box.get("valign", "Środek") != self.valign_var.get():
                self.selected_box["valign"] = self.valign_var.get()
                needs_pixel_update = True

            self.selected_box["ignored"] = False

            if needs_pixel_update:
                self.processor.image_changed = True
                self._save_global_state()
                self.redraw_canvas()

    def ignore_box(self):
        if self.selected_box:
            self.selected_box["ignored"] = True
            self.selected_box["new_text"] = None
            self.processor.image_changed = True
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()

    def revert_to_original(self):
        if self.selected_box:
            self.selected_box["ignored"] = False
            self.selected_box["new_text"] = None
            self.processor.image_changed = True
            self.update_sidebar()
            self._save_global_state()
            self.redraw_canvas()