import os
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk, ImageDraw
import numpy as np
import matplotlib.font_manager as fm
from image_processor import ImageProcessor

def get_system_fonts():
    """
    Pobiera fonty z metadanych systemowych, wyciąga nazwy rodzin oraz ich warianty
    (grubość i styl), a następnie buduje strukturę 3-poziomową dla menu kaskadowego.
    """
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

            # Odczyt i formatowanie metadanych wariantu
            w = str(font.weight).lower()
            w_name = weight_map.get(w, str(font.weight).title())
            style = "Italic" if font.style in ['italic', 'oblique'] else ""

            variant = f"{w_name} {style}".strip()
            if variant == "": variant = "Regular"
            if variant == "Regular Italic": variant = "Italic"

            unique_name = f"{family} - {variant}"

            # Dodawanie z preferencją TTF
            if unique_name in font_map:
                if ext == 'ttf' and font_map[unique_name].lower().endswith('.otf'):
                    font_map[unique_name] = path
            else:
                font_map[unique_name] = path

            # Grupowanie surowych fontów w układ Rodzina -> Wariant
            if family not in raw_fonts:
                raw_fonts[family] = {}

            if variant in raw_fonts[family]:
                if ext == 'ttf' and font_map[unique_name].lower().endswith('.otf'):
                     raw_fonts[family][variant] = unique_name
            else:
                 raw_fonts[family][variant] = unique_name
    except:
        font_map = {"Arial - Regular": "arial.ttf", "Times New Roman - Regular": "times.ttf"}
        raw_fonts = {"Arial": {"Regular": "Arial - Regular"}, "Times New Roman": {"Regular": "Times New Roman - Regular"}}

    menu_structure = {}

    # Grupowanie od A do Z oraz "Pozostałe"
    for family in sorted(raw_fonts.keys()):
        letter = family[0].upper()
        if not ('A' <= letter <= 'Z'):
            letter = "Pozostałe"

        if letter not in menu_structure:
            menu_structure[letter] = {}

        # Sortowanie wariantów (Regular zawsze pierwszy)
        variants = raw_fonts[family]
        sorted_variants = sorted(variants.keys(), key=lambda x: (0 if x in ['Regular', 'Normal'] else 1, x))

        menu_structure[letter][family] = [(v, variants[v]) for v in sorted_variants]

    return font_map, menu_structure

# FONT_MAP to płaski słownik (Unikalna Nazwa -> Ścieżka), GROUPED_FONTS to kategorie dla drzewka
FONT_MAP, GROUPED_FONTS = get_system_fonts()

class EditorWindow:
    def __init__(self, parent_frame, input_dir, engine, auto_translate, on_close_callback):
        self.frame = parent_frame
        self.input_dir = input_dir
        self.engine = engine
        self.auto_translate = auto_translate
        self.on_close = on_close_callback

        self.output_dir = os.path.join(input_dir, "przetlumaczone")
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir)

        self.image_files = [f for f in os.listdir(input_dir) if f.lower().endswith(('.tif', '.png', '.jpg'))]
        self.current_index = 0

        self.selected_box = None
        self.scale = 1.0
        self.offset_x = 0; self.offset_y = 0
        self.drawing_poly_mode = False
        self.current_poly_points = []
        self.temp_lines = []

        self._is_updating_sidebar = False

        self.setup_ui()
        self.load_image()

    def setup_ui(self):
        for widget in self.frame.winfo_children(): widget.destroy()

        self.canvas = tk.Canvas(self.frame, cursor="cross", bg="#333333")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.sidebar = tk.Frame(self.frame, width=350, bg="#f0f0f0", padx=15, pady=10)
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Label(self.sidebar, text="Narzędzia kształtów:", bg="#f0f0f0", font=("Arial", 10, "bold")).pack(anchor=tk.W)
        self.btn_draw = tk.Button(self.sidebar, text="[+] Narysuj Wielokąt (LPM: punkt, PPM: zamknij)", command=self.toggle_draw_mode, bg="#FFC107")
        self.btn_draw.pack(fill=tk.X, pady=(5, 5))

        self.btn_delete = tk.Button(self.sidebar, text="KOSZ: Usuń zaznaczone pole", command=self.delete_current_box, bg="#FF5252", fg="white")
        self.btn_delete.pack(fill=tk.X, pady=(0, 15))

        tk.Label(self.sidebar, text="Rozpoznano jako:", bg="#f0f0f0", fg="#555").pack(anchor=tk.W)

        self.txt_original = tk.Text(self.sidebar, height=3, width=30, font=("Arial", 11, "bold"), bg="#f0f0f0", relief=tk.FLAT)
        self.txt_original.pack(anchor=tk.W, pady=(0, 10))
        self.txt_original.config(state=tk.DISABLED)

        tk.Label(self.sidebar, text="Tekst zastępczy:", bg="#f0f0f0", fg="#555").pack(anchor=tk.W)

        tools_frame = tk.Frame(self.sidebar, bg="#f0f0f0")
        tools_frame.pack(fill=tk.X, pady=(0, 2))
        tk.Button(tools_frame, text="X² (Indeks górny)", command=self.insert_sup, font=("Arial", 9)).pack(side=tk.LEFT, padx=(0,5))
        tk.Button(tools_frame, text="X₂ (Indeks dolny)", command=self.insert_sub, font=("Arial", 9)).pack(side=tk.LEFT)

        self.text_new = tk.Text(self.sidebar, height=4, width=30, font=("Arial", 12))
        self.text_new.pack(fill=tk.X, pady=(0, 10))
        self.text_new.bind("<KeyRelease>", self.auto_apply)

        fmt_frame = tk.LabelFrame(self.sidebar, text="Ustawienia tekstu", bg="#f0f0f0", padx=5, pady=5)
        fmt_frame.pack(fill=tk.X, pady=5)

        tk.Label(fmt_frame, text="Font:", bg="#f0f0f0").grid(row=0, column=0, sticky=tk.W)

        # --- Konstrukcja Menu Kaskadowego (3 poziomy z wariantami) ---
        self.font_var = tk.StringVar(value="Arial - Regular")
        self.btn_font = ttk.Menubutton(fmt_frame, textvariable=self.font_var, width=15)
        self.btn_font.grid(row=0, column=1, padx=2, sticky=tk.EW)

        font_menu = tk.Menu(self.btn_font, tearoff=0)

        # Sortowanie liter poprawiające kolejność
        letters = sorted([k for k in GROUPED_FONTS.keys() if len(k) == 1])
        if "Pozostałe" in GROUPED_FONTS: letters.append("Pozostałe")

        for letter in letters:
            letter_menu = tk.Menu(font_menu, tearoff=0)
            families = GROUPED_FONTS[letter]

            for family in sorted(families.keys()):
                variants = families[family]

                # Jeśli jest tylko jeden wariant, dajemy go bezpośrednio
                if len(variants) == 1:
                    variant_name, unique_name = variants[0]
                    letter_menu.add_command(label=family, command=lambda f=unique_name: self.change_font(f))
                else:
                    # Dodatkowy poziom menu dla wariantów
                    family_menu = tk.Menu(letter_menu, tearoff=0)
                    for variant_name, unique_name in variants:
                        family_menu.add_command(label=variant_name, command=lambda f=unique_name: self.change_font(f))
                    letter_menu.add_cascade(label=family, menu=family_menu)

            font_menu.add_cascade(label=letter, menu=letter_menu)

        self.btn_font["menu"] = font_menu
        # -----------------------------------------------------------------

        self.size_var = tk.StringVar()
        tk.Label(fmt_frame, text="Rozmiar:", bg="#f0f0f0").grid(row=0, column=2, sticky=tk.W)
        self.spin_size = ttk.Spinbox(fmt_frame, from_=8, to=100, width=4, textvariable=self.size_var)
        self.spin_size.grid(row=0, column=3)
        self.size_var.trace_add("write", lambda *args: self.auto_apply())

        self.align_var = tk.StringVar()
        tk.Label(fmt_frame, text="Wyrównaj:", bg="#f0f0f0").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.combo_align = ttk.Combobox(fmt_frame, textvariable=self.align_var, values=["Lewo", "Środek", "Prawo"], state="readonly", width=14)
        self.combo_align.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=5)
        self.align_var.trace_add("write", lambda *args: self.auto_apply())

        self.spacing_var = tk.StringVar()
        tk.Label(fmt_frame, text="Interlinia:", bg="#f0f0f0").grid(row=2, column=0, sticky=tk.W)
        self.spin_spacing = ttk.Spinbox(fmt_frame, from_=-20, to=50, width=4, textvariable=self.spacing_var)
        self.spin_spacing.grid(row=2, column=1, sticky=tk.W)
        self.spacing_var.trace_add("write", lambda *args: self.auto_apply())

        shift_frame = tk.LabelFrame(self.sidebar, text="Przesunięcie względem tła (px)", bg="#f0f0f0", padx=5, pady=5)
        shift_frame.pack(fill=tk.X, pady=5)

        self.shift_x_var = tk.StringVar()
        tk.Label(shift_frame, text="Oś X:", bg="#f0f0f0").grid(row=0, column=0)
        self.spin_x = ttk.Spinbox(shift_frame, from_=-100, to=100, width=5, textvariable=self.shift_x_var)
        self.spin_x.grid(row=0, column=1, padx=5)
        self.shift_x_var.trace_add("write", lambda *args: self.auto_apply())

        self.shift_y_var = tk.StringVar()
        tk.Label(shift_frame, text="Oś Y:", bg="#f0f0f0").grid(row=0, column=2)
        self.spin_y = ttk.Spinbox(shift_frame, from_=-100, to=100, width=5, textvariable=self.shift_y_var)
        self.spin_y.grid(row=0, column=3, padx=5)
        self.shift_y_var.trace_add("write", lambda *args: self.auto_apply())

        btn_frame = tk.Frame(self.sidebar, bg="#f0f0f0")
        btn_frame.pack(fill=tk.X, pady=(15, 20))
        tk.Button(btn_frame, text="Ignoruj (Oryginał)", command=self.ignore_box).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(btn_frame, text="Przywróć (Cofnij)", command=self.revert_to_original).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        tk.Button(self.sidebar, text="Zapisz i Następny ->", command=self.next_image, bg="#2196F3", fg="white", font=("Arial", 11, "bold"), height=2).pack(fill=tk.X, pady=5)
        tk.Button(self.sidebar, text="Wyjdź do Menu", command=self.on_close).pack(fill=tk.X, pady=5)

        self.canvas.bind("<Configure>", self.redraw_canvas)
        self.canvas.bind("<ButtonPress-1>", self.on_left_click)
        self.canvas.bind("<ButtonPress-3>", self.on_right_click)

    def change_font(self, unique_font_name):
        if self._is_updating_sidebar: return
        self.font_var.set(unique_font_name)
        self.auto_apply()

    def insert_sub(self):
        self._insert_tag("_{", "}")

    def insert_sup(self):
        self._insert_tag("^{", "}")

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

    def toggle_draw_mode(self):
        self.drawing_poly_mode = not self.drawing_poly_mode
        self.current_poly_points = []
        for line in self.temp_lines: self.canvas.delete(line)
        self.temp_lines = []

        if self.drawing_poly_mode:
            self.btn_draw.config(bg="#4CAF50", text="[Trwa rysowanie wielokąta...]")
            self.canvas.config(cursor="crosshair")
            self.selected_box = None
            self.update_sidebar()
            self.redraw_canvas()
        else:
            self.btn_draw.config(bg="#FFC107", text="[+] Narysuj Wielokąt")
            self.canvas.config(cursor="cross")

    def load_image(self):
        if self.current_index >= len(self.image_files):
            self.on_close()
            return
        self.frame.master.title(f"Edytor - {self.image_files[self.current_index]}")
        img_path = os.path.join(self.input_dir, self.image_files[self.current_index])

        self.processor = ImageProcessor(img_path, self.engine, self.auto_translate)
        self.processor.detect_text()

        self.selected_box = None
        if self.drawing_poly_mode: self.toggle_draw_mode()
        self.update_sidebar()
        self.redraw_canvas()

    def redraw_canvas(self, event=None):
        if not hasattr(self, 'processor'): return
        canvas_w, canvas_h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10: return

        img_rgb = self.processor.get_rgb_image()
        orig_pil = Image.fromarray(img_rgb)
        img_w, img_h = orig_pil.size

        self.scale = min(canvas_w / img_w, canvas_h / img_h)
        if self.scale > 1.5: self.scale = 1.5

        new_w, new_h = int(img_w * self.scale), int(img_h * self.scale)
        self.offset_x = (canvas_w - new_w) // 2
        self.offset_y = (canvas_h - new_h) // 2

        resample = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
        resized_img = orig_pil.resize((new_w, new_h), resample)
        draw = ImageDraw.Draw(resized_img)

        for box in self.processor.boxes:
            color = "#00FF00" if box == self.selected_box else ("gray" if box["ignored"] else ("#FFA500" if box["new_text"] is not None else "red"))
            scaled_pts = [(int(p[0]*self.scale), int(p[1]*self.scale)) for p in box["points"]]
            if len(scaled_pts) > 2: draw.polygon(scaled_pts, outline=color, width=2)
            elif len(scaled_pts) == 2: draw.line(scaled_pts, fill=color, width=2)

        self.tk_img = ImageTk.PhotoImage(resized_img)
        self.canvas.delete("all")
        self.canvas.create_image(self.offset_x, self.offset_y, anchor=tk.NW, image=self.tk_img)

    def get_orig_coords(self, event_x, event_y):
        return (event_x - self.offset_x) / self.scale, (event_y - self.offset_y) / self.scale

    def point_in_polygon(self, pt, poly):
        x, y = pt
        n = len(poly)
        inside = False
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

        if self.drawing_poly_mode:
            self.current_poly_points.append((orig_x, orig_y))
            if len(self.current_poly_points) > 1:
                p1, p2 = self.current_poly_points[-2], self.current_poly_points[-1]
                x1, y1 = p1[0]*self.scale + self.offset_x, p1[1]*self.scale + self.offset_y
                x2, y2 = p2[0]*self.scale + self.offset_x, p2[1]*self.scale + self.offset_y
                line = self.canvas.create_line(x1, y1, x2, y2, fill="yellow", width=2)
                self.temp_lines.append(line)
            return

        clicked_box = None
        for box in reversed(self.processor.boxes):
            if self.point_in_polygon((orig_x, orig_y), box["points"]):
                clicked_box = box; break

        if clicked_box:
            self.selected_box = clicked_box
            self.update_sidebar()
            self.redraw_canvas()

    def on_right_click(self, event):
        if self.drawing_poly_mode and len(self.current_poly_points) > 2:
            new_box = self.processor.add_manual_polygon(self.current_poly_points)
            self.selected_box = new_box
            self.toggle_draw_mode()
            self.update_sidebar()
            self.redraw_canvas()

    def delete_current_box(self):
        if self.selected_box:
            self.processor.delete_box(self.selected_box["id"])
            self.selected_box = None
            self.update_sidebar()
            self.redraw_canvas()

    def update_sidebar(self):
        self._is_updating_sidebar = True

        self.text_new.delete("1.0", tk.END)
        self.size_var.set("")
        self.spacing_var.set("")
        self.shift_x_var.set("0")
        self.shift_y_var.set("0")

        widgets_to_toggle = [self.btn_font, self.spin_size, self.combo_align, self.spin_spacing, self.spin_x, self.spin_y]

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
            self.spacing_var.set(str(self.selected_box.get("line_spacing", 2)))
            self.shift_x_var.set(str(self.selected_box.get("shift_x", 0)))
            self.shift_y_var.set(str(self.selected_box.get("shift_y", 0)))

            for w in widgets_to_toggle:
                state_mode = "readonly" if isinstance(w, ttk.Combobox) else "normal"
                w.config(state=state_mode)

            self.text_new.focus()
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
        if getattr(self, "_is_updating_sidebar", False):
            return

        if self.selected_box:
            raw_text = self.text_new.get("1.0", tk.END)
            if raw_text.endswith('\n'): raw_text = raw_text[:-1]

            self.selected_box["new_text"] = raw_text
            self.selected_box["font_family"] = FONT_MAP.get(self.font_var.get(), "arial.ttf")

            try: self.selected_box["font_size"] = int(self.size_var.get())
            except ValueError: pass
            try: self.selected_box["line_spacing"] = int(self.spacing_var.get())
            except ValueError: pass
            try: self.selected_box["shift_x"] = int(self.shift_x_var.get())
            except ValueError: pass
            try: self.selected_box["shift_y"] = int(self.shift_y_var.get())
            except ValueError: pass

            self.selected_box["alignment"] = self.align_var.get()
            self.selected_box["ignored"] = False
            self.redraw_canvas()

    def ignore_box(self):
        if self.selected_box:
            self.selected_box["ignored"] = True
            self.selected_box["new_text"] = None
            self.update_sidebar()
            self.redraw_canvas()

    def revert_to_original(self):
        if self.selected_box:
            self.selected_box["ignored"] = False
            self.selected_box["new_text"] = None
            self.update_sidebar()
            self.redraw_canvas()

    def next_image(self):
        self.processor.save(os.path.join(self.output_dir, self.image_files[self.current_index]))
        self.current_index += 1
        self.load_image()