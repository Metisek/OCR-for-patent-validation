import os
# Flagi dla Paddle muszą być ZANIM zaimportujemy cokolwiek innego
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"

import cv2
import pytesseract
import numpy as np
import uuid
import warnings
import re
from PIL import Image, ImageDraw, ImageFont
from deep_translator import GoogleTranslator

_GLOBAL_PADDLE_READER = None

# Ustaw swoją ścieżkę do Tesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Users\mbojarski\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
warnings.filterwarnings("ignore", category=FutureWarning)

class ImageProcessor:
    def __init__(self, image_path, engine="tesseract", auto_translate=False):
        self.image_path = image_path
        self.engine = engine
        self.auto_translate = auto_translate

        # Wymuszenie jednostronnego tłumaczenia z angielskiego na polski
        self.translator = GoogleTranslator(source='en', target='pl') if auto_translate else None

        self.original_cv_image = cv2.imread(image_path)
        self.current_cv_image = self.original_cv_image.copy()
        self.boxes = []

        # Puste instancje dla silników
        self.easyocr_reader = None

    def _strip_punctuation(self, text):
        """Usuwa spacje i znaki interpunkcyjne do porównania (np. 'RS' == ',,RS')"""
        return ''.join(c for c in text if c.isalnum()).lower()

    def detect_text(self):
        self.boxes = []
        gray = cv2.cvtColor(self.original_cv_image, cv2.COLOR_BGR2GRAY)

        global _GLOBAL_PADDLE_READER # Używamy modelu globalnego

        if self.engine == "paddleocr":
            if _GLOBAL_PADDLE_READER is None:
                print("Ładowanie modelu PaddleOCR do pamięci RAM (to nastąpi tylko raz!)...")
                from paddleocr import PaddleOCR
                _GLOBAL_PADDLE_READER = PaddleOCR(use_angle_cls=True, lang='en')

            img_rgb = cv2.cvtColor(self.original_cv_image, cv2.COLOR_BGR2RGB)
            results = _GLOBAL_PADDLE_READER.ocr(img_rgb, cls=True)

            if results and results[0]:
                for line in results[0]:
                    bbox = line[0]
                    text = line[1][0]

                    if text and not all(c in '|/\\-_()[]{}.~`I=:' for c in text):
                        pts = [(int(pt[0]), int(pt[1])) for pt in bbox]
                        x, y, w, h = cv2.boundingRect(np.array(pts))
                        pad = 2
                        x, y, w, h = max(0, x-pad), max(0, y-pad), w+2*pad, h+2*pad
                        straight_pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
                        self._append_box(straight_pts, x, y, w, h, text)

        elif self.engine == "easyocr":
            if self.easyocr_reader is None:
                import easyocr
                self.easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)

            # --- POPRAWKA SKUTECZNOŚCI EASYOCR ---
            # mag_ratio=1.5 sztucznie powiększa obraz, pomagając z małymi indeksami
            # text_threshold i low_text obniżają rygorystyczność wykrywania
            results = self.easyocr_reader.readtext(
                gray,
                text_threshold=0.15,
                low_text=0.15,
                mag_ratio=1.5,
                width_ths=0.5,
                link_threshold=0.4,
                paragraph=False
            )

            for (bbox, text, prob) in results:
                if text and not all(c in '|/\\-_()[]{}.~`I=:' for c in text):
                    pts = [(int(pt[0]), int(pt[1])) for pt in bbox]
                    x, y, w, h = cv2.boundingRect(np.array(pts))
                    straight_pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
                    self._append_box(straight_pts, x, y, w, h, text)

        else: # Tesseract (Domyślny / Fallback)
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            # Używamy standardowego PSM 11 dla tekstu porozrzucanego
            d = pytesseract.image_to_data(thresh, config='--oem 3 --psm 11', output_type=pytesseract.Output.DICT)
            for i in range(len(d['text'])):
                if int(d['conf'][i]) > 10:
                    text = d['text'][i].strip()
                    if text and not all(c in '|/\\-_()[]{}.~`I=:' for c in text):
                        x, y, w, h = d['left'][i], d['top'][i], d['width'][i], d['height'][i]
                        pad = 2
                        x, y, w, h = max(0, x-pad), max(0, y-pad), w+2*pad, h+2*pad
                        pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
                        self._append_box(pts, x, y, w, h, text)

        return self.boxes

    def _append_box(self, pts, x, y, w, h, text):
        text = text.strip()

        est_size = max(10, int(h * 0.8))

        translated_text = None
        new_text_val = None

        if self.auto_translate and len(text) >= 2 and any(c.isalpha() for c in text):
            try:
                translated = self.translator.translate(text)
                if translated:
                    # Zmiana: Ignoruj zmiany polegające TYLKO na interpunkcji
                    orig_clean = self._strip_punctuation(text)
                    trans_clean = self._strip_punctuation(translated)

                    if orig_clean != trans_clean and len(trans_clean) > 0:
                        translated_text = translated
                        new_text_val = translated
            except:
                pass

        self.boxes.append({
            "id": str(uuid.uuid4()),
            "points": pts,
            "x": x, "y": y, "w": w, "h": h,
            "original_text": text,
            "translated_text": translated_text,
            "new_text": new_text_val,
            "ignored": False,
            "font_family": "arial.ttf",
            "font_size": est_size,
            "alignment": "Środek",
            "line_spacing": 2,
            "shift_x": 0, "shift_y": 0
        })

    def add_manual_polygon(self, pts):
        pts_array = np.array(pts, dtype=np.int32)
        x, y, w, h = cv2.boundingRect(pts_array)

        mask = np.zeros(self.original_cv_image.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [pts_array], 255)
        masked_img = cv2.bitwise_and(self.original_cv_image, self.original_cv_image, mask=mask)

        detected_text = "[Pole Niestandardowe]"
        if self.engine == "easyocr" and self.easyocr_reader:
            res = self.easyocr_reader.readtext(masked_img)
            if res: detected_text = " ".join([r[1] for r in res])
        else:
            text = pytesseract.image_to_string(masked_img, config=r'--psm 6').strip()
            if text: detected_text = text

        est_size = max(10, int(h * 0.8))
        new_box = {
            "id": str(uuid.uuid4()),
            "points": pts,
            "x": x, "y": y, "w": w, "h": h,
            "original_text": detected_text,
            "translated_text": None,
            "new_text": None,
            "ignored": False,
            "font_family": "arial.ttf",
            "font_size": est_size,
            "alignment": "Środek",
            "line_spacing": 2,
            "shift_x": 0, "shift_y": 0
        }
        self.boxes.append(new_box)
        return new_box

    def delete_box(self, box_id):
        self.boxes = [b for b in self.boxes if b["id"] != box_id]

    def _parse_rich_text(self, line):
        tokens = []
        pattern = re.compile(r'(\^\{([^}]+)\})|(_\{([^}]+)\})')
        last_end = 0
        for match in pattern.finditer(line):
            if match.start() > last_end:
                tokens.append(('normal', line[last_end:match.start()]))
            if match.group(1):
                tokens.append(('sup', match.group(2)))
            elif match.group(3):
                tokens.append(('sub', match.group(4)))
            last_end = match.end()
        if last_end < len(line):
            tokens.append(('normal', line[last_end:]))
        return tokens

    def apply_all_edits(self):
        img = self.original_cv_image.copy()

        for box in self.boxes:
            if box["ignored"] or box["new_text"] is None or not str(box["new_text"]).strip():
                continue

            pts_array = np.array(box["points"], dtype=np.int32)
            cv2.fillPoly(img, [pts_array], (255, 255, 255))

            new_text = str(box["new_text"])
            img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(img_pil)

            try:
                font_normal = ImageFont.truetype(box["font_family"], int(box["font_size"]))
                font_small = ImageFont.truetype(box["font_family"], max(8, int(box["font_size"] * 0.65)))
            except IOError:
                font_normal = font_small = ImageFont.load_default()

            x, y, w, h = box["x"], box["y"], box["w"], box["h"]
            spacing = box.get("line_spacing", 2)

            lines = new_text.split('\n')
            parsed_lines = [self._parse_rich_text(l) for l in lines]

            line_widths = []
            line_heights = []

            for p_line in parsed_lines:
                lw = 0
                lh = box["font_size"]
                for type_, txt in p_line:
                    f = font_small if type_ in ['sup', 'sub'] else font_normal
                    try: lw += draw.textlength(txt, font=f)
                    except AttributeError: lw += draw.textsize(txt, font=f)[0]
                line_widths.append(lw)
                line_heights.append(lh)

            total_h = sum(line_heights) + (len(lines) - 1) * spacing
            current_y = y + (h - total_h) / 2 + box.get("shift_y", 0)

            for i, p_line in enumerate(parsed_lines):
                lw = line_widths[i]
                if box["alignment"] == "Lewo": tx = x
                elif box["alignment"] == "Prawo": tx = x + w - lw
                else: tx = x + (w - lw) / 2

                current_x = tx + box.get("shift_x", 0)

                for type_, txt in p_line:
                    f = font_small if type_ in ['sup', 'sub'] else font_normal
                    y_offset = 0
                    if type_ == 'sup': y_offset = -box["font_size"] * 0.35
                    elif type_ == 'sub': y_offset = box["font_size"] * 0.35

                    draw.text((current_x, current_y + y_offset), txt, font=f, fill=(0, 0, 0))
                    try: current_x += draw.textlength(txt, font=f)
                    except AttributeError: current_x += draw.textsize(txt, font=f)[0]

                current_y += line_heights[i] + spacing

            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

        self.current_cv_image = img

    def get_rgb_image(self):
        self.apply_all_edits()
        return cv2.cvtColor(self.current_cv_image, cv2.COLOR_BGR2RGB)

    def save(self, output_path):
        self.apply_all_edits()
        cv2.imwrite(output_path, self.current_cv_image)