import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE" # Rozwiązanie błędu OMP dla EasyOCR + PaddleOCR

import tkinter as tk
from tkinter import filedialog, messagebox
import tempfile
import zipfile
from editor import EditorWindow
from PIL import Image, ImageTk # Dodane do obsługi ikonek PNG w Tkinter

class AppLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("Tłumacz OCR - Wybór Źródła")

        # --- ŁADOWANIE IKONY APLIKACJI ---
        self.set_app_icon()

        self.selected_path = tk.StringVar(value="Nie wybrano źródła...")
        self.engine_var = tk.StringVar(value="none")
        self.translate_var = tk.BooleanVar(value=False)
        self.temp_dir = None

        self.create_menu()

        self.main_frame = tk.Frame(self.root, padx=20, pady=20)
        self.main_frame.pack(expand=True, fill=tk.BOTH)

        self.build_launcher_ui()

    def set_app_icon(self):
        """Próbuje załadować plik icon.png lub icon.ico z folderu aplikacji"""
        icon_path_png = os.path.join(os.path.dirname(__file__), 'icon.png')
        icon_path_ico = os.path.join(os.path.dirname(__file__), 'icon.ico')

        try:
            if os.path.exists(icon_path_png):
                # Obsługa PNG przez PIL (uniwersalne)
                icon_img = Image.open(icon_path_png)
                self.icon_photo = ImageTk.PhotoImage(icon_img) # Musi być zachowane w self, inaczej garbage collector usunie
                self.root.iconphoto(True, self.icon_photo)
            elif os.path.exists(icon_path_ico):
                # Natywna obsługa ICO dla Windows
                self.root.iconbitmap(icon_path_ico)
        except Exception as e:
            print(f"Nie udało się załadować ikony aplikacji: {e}")

    def create_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Zakończ", command=self.root.quit)
        menubar.add_cascade(label="Plik", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Autor", command=self.show_author)
        menubar.add_cascade(label="O programie", menu=help_menu)

        self.root.config(menu=menubar)

    def show_author(self):
        messagebox.showinfo(
            "Autor",
            "Tłumacz OCR\n\n"
            "Autor: Mateusz Bojarski\n"
            "Do wewnętrznego użytku w AOMB Polska Sp. z o.o.\n"
            "Wersja 1.0"
        )

    def build_launcher_ui(self):
        # Przywracanie tytułu okna w razie powrotu z edytora
        self.root.title("Tłumacz OCR - Wybór Źródła")

        for widget in self.main_frame.winfo_children():
            widget.destroy()

        self.root.geometry("480x450")

        tk.Label(self.main_frame, text="1. Wybierz źródło rysunków", font=("Arial", 12, "bold")).pack(pady=(10, 5))

        btn_frame = tk.Frame(self.main_frame)
        btn_frame.pack(pady=5)
        tk.Button(btn_frame, text="Wybierz Folder...", command=self.browse_directory, width=20).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Wybierz Plik ZIP...", command=self.browse_zip, width=20).pack(side=tk.LEFT, padx=5)

        tk.Label(self.main_frame, textvariable=self.selected_path, fg="#555", wraplength=400).pack(pady=(5, 15))

        tk.Label(self.main_frame, text="2. Konfiguracja silnika", font=("Arial", 12, "bold")).pack(pady=(5, 5))

        engine_frame = tk.Frame(self.main_frame)
        engine_frame.pack(pady=5, anchor=tk.W)
        tk.Radiobutton(engine_frame, text="Brak OCR (edycja w pełni ręczna)", variable=self.engine_var, value="none").pack(anchor=tk.W)
        tk.Radiobutton(engine_frame, text="EasyOCR (AI, klasyczny tekst w dokumentach)", variable=self.engine_var, value="easyocr").pack(anchor=tk.W)
        tk.Radiobutton(engine_frame, text="PaddleOCR (AI, rekomendowany do rysunków/wykresów)", variable=self.engine_var, value="paddleocr").pack(anchor=tk.W)

        options_frame = tk.Frame(self.main_frame)
        options_frame.pack(pady=10, anchor=tk.W)
        tk.Checkbutton(options_frame, text="Automatyczne tłumaczenie wykrytych słów (EN -> PL)", variable=self.translate_var).pack(anchor=tk.W)

        tk.Button(self.main_frame, text="Rozpocznij przetwarzanie", command=self.start_processing, bg="#0052cc", fg="white", font=("Arial", 12, "bold"), width=25).pack(pady=25)

    def browse_directory(self):
        directory = filedialog.askdirectory(title="Wybierz folder z rysunkami")
        if directory:
            self.selected_path.set(directory)

    def browse_zip(self):
        filepath = filedialog.askopenfilename(title="Wybierz plik ZIP", filetypes=[("Pliki ZIP", "*.zip")])
        if filepath:
            self.selected_path.set(filepath)

    def start_processing(self):
        path = self.selected_path.get()
        if path == "Nie wybrano źródła...":
            messagebox.showwarning("Błąd", "Najpierw wskaż folder lub plik ZIP!")
            return

        original_zip = None
        if path.lower().endswith('.zip'):
            if not os.path.isfile(path):
                messagebox.showwarning("Błąd", "Wskazany plik ZIP nie istnieje na dysku!")
                return
            self.temp_dir = tempfile.TemporaryDirectory()
            try:
                with zipfile.ZipFile(path, 'r') as zip_ref:
                    zip_ref.extractall(self.temp_dir.name)
            except Exception as e:
                messagebox.showerror("Błąd archiwum", f"Nie udało się rozpakować pliku ZIP:\n{str(e)}")
                return
            input_dir = self.temp_dir.name
            original_zip = path
        else:
            if not os.path.isdir(path):
                messagebox.showwarning("Błąd", "Wskaż poprawny folder na dysku!")
                return
            input_dir = path

        engine = self.engine_var.get()
        auto_translate = self.translate_var.get()
        EditorWindow(self.main_frame, input_dir, engine, auto_translate, self.build_launcher_ui, original_zip_path=original_zip)

if __name__ == "__main__":
    root = tk.Tk()
    app = AppLauncher(root)
    root.mainloop()