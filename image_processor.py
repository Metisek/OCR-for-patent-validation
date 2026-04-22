import os
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"

import cv2
import pytesseract
import numpy as np
import uuid
import warnings
import re
import math
from PIL import Image, ImageDraw, ImageFont
from deep_translator import GoogleTranslator

_GLOBAL_PADDLE_READER = None
pytesseract.pytesseract.tesseract_cmd = r'C:\Users\mbojarski\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
warnings.filterwarnings("ignore", category=FutureWarning)

TRANSLATION_FIXES = {
    r"(?i)\bfiga?\.?\s*(\d+)\b": r"Fig. \1",
    r"(?i)\bf[l|i]g\b\.?\s*(\d+)": r"Fig. \1"
}

class ImageProcessor:
    def __init__(self, image_path, engine="tesseract", auto_translate=False):
        self.image_path = image_path
        self.engine = engine
        self.auto_translate = auto_translate
        self.translator = GoogleTranslator(source='en', target='pl') if auto_translate else None

        # ROZWIĄZANIE PROBLEMU Z POLSKIMI ZNAKAMI W ŚCIEŻKACH:
        with open(image_path, "rb") as f:
            chunk = f.read()
        chunk_arr = np.frombuffer(chunk, dtype=np.uint8)
        self.original_cv_image = cv2.imdecode(chunk_arr, cv2.IMREAD_COLOR)

        if self.original_cv_image is None:
            raise ValueError(f"Nie udało się odczytać obrazu (uszkodzony plik?): {image_path}")

        self.current_cv_image = self.original_cv_image.copy()
        self.boxes = []
        self.easyocr_reader = None

    def _strip_punctuation(self, text):
        return ''.join(c for c in text if c.isalnum()).lower()

    def _calculate_snapped_angle(self, pts):
        pt1, pt2 = pts[0], pts[1]
        dx = pt2[0] - pt1[0]
        dy = pt2[1] - pt1[1]
        raw_angle = math.degrees(math.atan2(dy, dx))

        snapped = round(raw_angle / 45.0) * 45.0

        if snapped <= -180: snapped += 360
        elif snapped > 180: snapped -= 360
        return snapped

    def detect_text(self):
        self.boxes = []
        gray = cv2.cvtColor(self.original_cv_image, cv2.COLOR_BGR2GRAY)
        global _GLOBAL_PADDLE_READER

        if self.engine == "paddleocr":
            if _GLOBAL_PADDLE_READER is None:
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
                        angle = self._calculate_snapped_angle(pts)
                        self._append_box(pts, text, angle)

        elif self.engine == "easyocr":
            if self.easyocr_reader is None:
                import easyocr
                self.easyocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)

            results = self.easyocr_reader.readtext(
                gray, text_threshold=0.15, low_text=0.15, mag_ratio=1.5, width_ths=0.5, link_threshold=0.4, paragraph=False
            )

            for (bbox, text, prob) in results:
                if text and not all(c in '|/\\-_()[]{}.~`I=:' for c in text):
                    pts = [(int(pt[0]), int(pt[1])) for pt in bbox]
                    angle = self._calculate_snapped_angle(pts)
                    self._append_box(pts, text, angle)

        else: # Tesseract
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            d = pytesseract.image_to_data(thresh, config='--oem 3 --psm 11', output_type=pytesseract.Output.DICT)
            for i in range(len(d['text'])):
                if int(d['conf'][i]) > 10:
                    text = d['text'][i].strip()
                    if text and not all(c in '|/\\-_()[]{}.~`I=:' for c in text):
                        x, y, w, h = d['left'][i], d['top'][i], d['width'][i], d['height'][i]
                        pad = 2
                        x, y, w, h = max(0, x-pad), max(0, y-pad), w+2*pad, h+2*pad
                        pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
                        self._append_box(pts, text, 0.0)

        return self.boxes

    def _append_box(self, pts, text, angle):
        text = text.strip()
        h_est = max(10, int(np.linalg.norm(np.array(pts[1]) - np.array(pts[2])) * 0.8))

        translated_text = None
        new_text_val = None

        if self.auto_translate and len(text) >= 2 and any(c.isalpha() for c in text):
            try:
                translated = self.translator.translate(text)
                if translated:
                    for pattern, replacement in TRANSLATION_FIXES.items():
                        translated = re.sub(pattern, replacement, translated)

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
            "angle": angle,
            "original_text": text,
            "translated_text": translated_text,
            "new_text": new_text_val,
            "ignored": False,
            "font_family": "arial.ttf",
            "font_size": h_est,
            "alignment": "Środek",
            "line_spacing": 2,
            "shift_x": 0, "shift_y": 0
        })

    def add_manual_polygon(self, pts):
        pts_array = np.array(pts, dtype=np.int32)
        angle = self._calculate_snapped_angle(pts)

        mask = np.zeros(self.original_cv_image.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [pts_array], 255)
        masked_img = cv2.bitwise_and(self.original_cv_image, self.original_cv_image, mask=mask)

        detected_text = "[Pole Niestandardowe]"
        if self.engine == "easyocr" and self.easyocr_reader:
            res = self.easyocr_reader.readtext(masked_img)
            if res: detected_text = " ".join([r[1] for r in res])
        else:
            text = pytesseract.image_to_string(masked_img, config=r'--psm 3').strip()
            if text: detected_text = text

        h_est = max(10, int(np.linalg.norm(np.array(pts[1]) - np.array(pts[2])) * 0.8))
        new_box = {
            "id": str(uuid.uuid4()),
            "points": pts,
            "angle": angle,
            "original_text": detected_text,
            "translated_text": None,
            "new_text": None,
            "ignored": False,
            "font_family": "arial.ttf",
            "font_size": h_est,
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
        img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

        for box in self.boxes:
            if box["ignored"] or box["new_text"] is None or not str(box["new_text"]).strip():
                continue

            pts_array = np.array(box["points"], dtype=np.int32)
            cv2.fillPoly(img, [pts_array], (255, 255, 255))
            img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

            try:
                font_normal = ImageFont.truetype(box["font_family"], int(box["font_size"]))
                font_small = ImageFont.truetype(box["font_family"], max(8, int(box["font_size"] * 0.65)))
            except IOError:
                font_normal = font_small = ImageFont.load_default()

            spacing = box.get("line_spacing", 2)
            lines = str(box["new_text"]).split('\n')
            parsed_lines = [self._parse_rich_text(l) for l in lines]

            temp_img = Image.new('RGBA', (1, 1))
            draw_temp = ImageDraw.Draw(temp_img)

            line_widths = []
            line_heights = []
            for p_line in parsed_lines:
                lw = 0
                lh = box["font_size"]
                for type_, txt in p_line:
                    f = font_small if type_ in ['sup', 'sub'] else font_normal
                    try: lw += draw_temp.textlength(txt, font=f)
                    except AttributeError: lw += draw_temp.textsize(txt, font=f)[0]
                line_widths.append(lw)
                line_heights.append(lh)

            text_width = max(line_widths) if line_widths else 10
            text_height = sum(line_heights) + (len(lines) - 1) * spacing if line_heights else 10

            pad = 4
            txt_img = Image.new('RGBA', (int(text_width) + pad*2, int(text_height) + pad*2), (255, 255, 255, 0))
            draw = ImageDraw.Draw(txt_img)

            current_y = pad
            for i, p_line in enumerate(parsed_lines):
                lw = line_widths[i]
                if box["alignment"] == "Lewo": tx = pad
                elif box["alignment"] == "Prawo": tx = pad + text_width - lw
                else: tx = pad + (text_width - lw) / 2

                current_x = tx
                for type_, txt in p_line:
                    f = font_small if type_ in ['sup', 'sub'] else font_normal
                    y_offset = -box["font_size"] * 0.35 if type_ == 'sup' else (box["font_size"] * 0.35 if type_ == 'sub' else 0)
                    draw.text((current_x, current_y + y_offset), txt, font=f, fill=(0, 0, 0, 255))
                    try: current_x += draw.textlength(txt, font=f)
                    except AttributeError: current_x += draw.textsize(txt, font=f)[0]
                current_y += line_heights[i] + spacing

            angle = box.get("angle", 0.0)
            rotated_txt_img = txt_img.rotate(-angle, expand=True, resample=Image.BICUBIC)

            cx = sum(p[0] for p in box["points"]) / 4.0 + box.get("shift_x", 0)
            cy = sum(p[1] for p in box["points"]) / 4.0 + box.get("shift_y", 0)
            paste_x = int(cx - rotated_txt_img.width / 2)
            paste_y = int(cy - rotated_txt_img.height / 2)

            img_pil.paste(rotated_txt_img, (paste_x, paste_y), rotated_txt_img)
            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

        self.current_cv_image = img

    def get_rgb_image(self):
        self.apply_all_edits()
        return cv2.cvtColor(self.current_cv_image, cv2.COLOR_BGR2RGB)

    def save(self, output_path):
        self.apply_all_edits()
        # ROZWIĄZANIE ZAPISU PLIKU PRZY POLSKICH ZNAKACH W ŚCIEŻCE
        ext = os.path.splitext(output_path)[1]
        is_success, im_buf_arr = cv2.imencode(ext, self.current_cv_image)
        if is_success:
            with open(output_path, "wb") as f:
                im_buf_arr.tofile(f)
        else:
            print(f"Nie udało się zapisać pliku: {output_path}")