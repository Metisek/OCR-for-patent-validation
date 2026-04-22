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

        with open(image_path, "rb") as f:
            chunk = f.read()
        chunk_arr = np.frombuffer(chunk, dtype=np.uint8)
        self.original_cv_image = cv2.imdecode(chunk_arr, cv2.IMREAD_COLOR)

        if self.original_cv_image is None:
            raise ValueError(f"Nie udało się odczytać obrazu (uszkodzony plik?): {image_path}")

        self.current_cv_image = self.original_cv_image.copy()
        self.boxes = []
        self.easyocr_reader = None

        self.image_changed = True
        self._cached_rgb = None

    def _strip_punctuation(self, text):
        return ''.join(c for c in text if c.isalnum()).lower()

    def _regularize_box(self, pts):
        pt0, pt1, pt2, pt3 = pts
        dx1, dy1 = pt1[0] - pt0[0], pt1[1] - pt0[1]
        dx2, dy2 = pt2[0] - pt1[0], pt2[1] - pt1[1]
        len1, len2 = math.hypot(dx1, dy1), math.hypot(dx2, dy2)

        if len1 == 0: len1 = 1
        if len2 == 0: len2 = 1

        if len2 / len1 >= 2.25:
            snapped_angle = -90.0
        elif len1 / len2 >= 2.25:
            snapped_angle = 0.0
        else:
            if len1 >= len2:
                raw_angle = math.degrees(math.atan2(dy1, dx1))
            else:
                raw_angle = math.degrees(math.atan2(dy2, dx2)) - 90
            snapped_angle = round(raw_angle / 45.0) * 45.0

        if snapped_angle <= -180: snapped_angle += 360
        elif snapped_angle > 180: snapped_angle -= 360

        cx = sum(p[0] for p in pts) / 4.0
        cy = sum(p[1] for p in pts) / 4.0

        rad = math.radians(-snapped_angle)
        cos_a, sin_a = math.cos(rad), math.sin(rad)

        unrotated = []
        for p in pts:
            rx = cx + (p[0] - cx) * cos_a - (p[1] - cy) * sin_a
            ry = cy + (p[0] - cx) * sin_a + (p[1] - cy) * cos_a
            unrotated.append((rx, ry))

        min_x, max_x = min(p[0] for p in unrotated), max(p[0] for p in unrotated)
        min_y, max_y = min(p[1] for p in unrotated), max(p[1] for p in unrotated)

        ideal_unrotated = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

        rad_back = math.radians(snapped_angle)
        cos_b, sin_b = math.cos(rad_back), math.sin(rad_back)

        final_pts = []
        for p in ideal_unrotated:
            rx = cx + (p[0] - cx) * cos_b - (p[1] - cy) * sin_b
            ry = cy + (p[0] - cx) * sin_b + (p[1] - cy) * cos_b
            final_pts.append((rx, ry))

        return final_pts, snapped_angle

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
                        pts, angle = self._regularize_box(pts)
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
                    pts, angle = self._regularize_box(pts)
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

        self.image_changed = True
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
            "valign": "Środek",
            "line_spacing": 2,
            "shift_x": 0, "shift_y": 0
        })

    def add_manual_polygon(self, pts):
        pts_array = np.array(pts, dtype=np.int32)
        x, y, w, h = cv2.boundingRect(pts_array)
        rect_pts = [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
        angle = 0.0

        mask = np.zeros(self.original_cv_image.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(rect_pts)], 255)
        masked_img = cv2.bitwise_and(self.original_cv_image, self.original_cv_image, mask=mask)

        detected_text = "[Pole Niestandardowe]"
        if self.engine == "easyocr" and self.easyocr_reader:
            res = self.easyocr_reader.readtext(masked_img)
            if res: detected_text = " ".join([r[1] for r in res])
        else:
            text = pytesseract.image_to_string(masked_img, config=r'--psm 3').strip()
            if text: detected_text = text

        h_est = max(10, int(h * 0.8))
        new_box = {
            "id": str(uuid.uuid4()),
            "points": rect_pts,
            "angle": angle,
            "original_text": detected_text,
            "translated_text": None,
            "new_text": None,
            "ignored": False,
            "font_family": "arial.ttf",
            "font_size": h_est,
            "alignment": "Środek",
            "valign": "Środek",
            "line_spacing": 2,
            "shift_x": 0, "shift_y": 0
        }
        self.boxes.append(new_box)
        self.image_changed = True
        return new_box

    def delete_box(self, box_id):
        self.boxes = [b for b in self.boxes if b["id"] != box_id]
        self.image_changed = True

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

            pts = box["points"]
            # Matematyczne długości boków tła dla idealnej nowej warstwy
            box_w = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
            box_h = math.hypot(pts[3][0] - pts[0][0], pts[3][1] - pts[0][1])

            pad = 2
            # Kanwa ma rozmiar dopasowany do większego elementu (Tekst vs Ramka tła)
            canvas_w = max(text_width, box_w) + pad*2
            canvas_h = max(text_height, box_h) + pad*2

            txt_img = Image.new('RGBA', (int(canvas_w), int(canvas_h)), (255, 255, 255, 0))
            draw = ImageDraw.Draw(txt_img)

            # Podstawa justowania poziomego
            if box["alignment"] == "Lewo": base_tx = pad
            elif box["alignment"] == "Prawo": base_tx = canvas_w - text_width - pad
            else: base_tx = (canvas_w - text_width) / 2.0

            # Podstawa justowania pionowego
            if box.get("valign", "Środek") == "Góra": base_ty = pad
            elif box.get("valign", "Środek") == "Dół": base_ty = canvas_h - text_height - pad
            else: base_ty = (canvas_h - text_height) / 2.0

            current_y = base_ty
            for i, p_line in enumerate(parsed_lines):
                lw = line_widths[i]
                # Pociągnięcie wyjustowania dla pojedynczych linijek w bloku tekstu
                if box["alignment"] == "Lewo": tx = base_tx
                elif box["alignment"] == "Prawo": tx = base_tx + (text_width - lw)
                else: tx = base_tx + (text_width - lw) / 2.0

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

            # Srodek geometryczny tła jest też zawsze idealnym środkiem wirtualnej kanwy
            cx = sum(p[0] for p in pts) / 4.0 + box.get("shift_x", 0)
            cy = sum(p[1] for p in pts) / 4.0 + box.get("shift_y", 0)

            paste_x = int(cx - rotated_txt_img.width / 2.0)
            paste_y = int(cy - rotated_txt_img.height / 2.0)

            img_pil.paste(rotated_txt_img, (paste_x, paste_y), rotated_txt_img)
            img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

        self.current_cv_image = img
        self._cached_rgb = np.array(img_pil)
        self.image_changed = False

    def get_rgb_image(self):
        if self.image_changed or self._cached_rgb is None:
            self.apply_all_edits()
        return self._cached_rgb

    def save(self, output_path):
        self.apply_all_edits()
        ext = os.path.splitext(output_path)[1]
        is_success, im_buf_arr = cv2.imencode(ext, self.current_cv_image)
        if is_success:
            with open(output_path, "wb") as f:
                im_buf_arr.tofile(f)