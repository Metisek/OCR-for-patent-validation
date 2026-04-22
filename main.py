import tkinter as tk
from tkinter import filedialog, messagebox
import os
from editor import EditorWindow

class AppLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("Tłumacz Rysunków - Wybór Silnika")
        self.selected_path = tk.StringVar(value="Nie wybrano katalogu...")
        self.engine_var = tk.StringVar(value="tesseract")
        self.translate_var = tk.BooleanVar(value=False)

        self.main_frame = tk.Frame(self.root, padx=20, pady=20)
        self.main_frame.pack(expand=True, fill=tk.BOTH)

        self.build_launcher_ui()

    def build_launcher_ui(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()

        self.root.geometry("450x450")

        tk.Label(self.main_frame, text="1. Wybierz katalog TIF/PNG", font=("Arial", 12, "bold")).pack(pady=(10, 5))
        tk.Button(self.main_frame, text="Przeglądaj...", command=self.browse_directory, width=20).pack()
        tk.Label(self.main_frame, textvariable=self.selected_path, fg="#555", wraplength=400).pack(pady=(5, 15))

        tk.Label(self.main_frame, text="2. Konfiguracja", font=("Arial", 12, "bold")).pack(pady=(5, 5))

        engine_frame = tk.Frame(self.main_frame)
        engine_frame.pack(pady=5, anchor=tk.W)
        tk.Radiobutton(engine_frame, text="Tesseract (Szybszy, bazowy)", variable=self.engine_var, value="tesseract").pack(anchor=tk.W)
        tk.Radiobutton(engine_frame, text="EasyOCR (AI, klasyczny tekst)", variable=self.engine_var, value="easyocr").pack(anchor=tk.W)
        tk.Radiobutton(engine_frame, text="PaddleOCR (AI, najlepszy do rysunków/schematów)", variable=self.engine_var, value="paddleocr").pack(anchor=tk.W)

        options_frame = tk.Frame(self.main_frame)
        options_frame.pack(pady=10, anchor=tk.W)
        tk.Checkbutton(options_frame, text="Automatyczne tłumaczenie wykrytych słów (EN -> PL)", variable=self.translate_var).pack(anchor=tk.W)

        tk.Button(self.main_frame, text="Rozpocznij przetwarzanie", command=self.start_processing, bg="#0052cc", fg="white", font=("Arial", 12, "bold"), width=25).pack(pady=25)

    def browse_directory(self):
        directory = filedialog.askdirectory(title="Wybierz folder")
        if directory:
            self.selected_path.set(directory)

    def start_processing(self):
        path = self.selected_path.get()
        if path == "Nie wybrano katalogu..." or not os.path.isdir(path):
            messagebox.showwarning("Błąd", "Wskaż poprawny katalog!")
            return

        engine = self.engine_var.get()
        auto_translate = self.translate_var.get()
        EditorWindow(self.main_frame, path, engine, auto_translate, self.build_launcher_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = AppLauncher(root)
    root.mainloop()