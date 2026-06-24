



import random
import string
import config
from collections import namedtuple
from PIL import Image, ImageDraw, ImageFont
import tempfile

Captcha = namedtuple('Captcha', 'problem solution')
Result = namedtuple('Result', 'filename solution hint_key')

RED_FACTOR = 0.299
GREEN_FACTOR = 0.587
BLUE_FACTOR = 0.114
THRESHOLD = 150


class CaptchaGenerator:
    def generate_math_problem(self):
        lhs = random.randint(1, 10)
        rhs = random.randint(1, 10)
        operator = random.choice(('+', '-', '*'))
        problem = f'{lhs} {operator} {rhs}'
        return Captcha(problem, str(eval(problem)))

    def generate_text_problem(self):
        chars = string.ascii_uppercase + string.digits
        text = ''.join(random.choices(chars, k=random.randint(4, 5)))
        return Captcha(text, text.upper())

    def generate_sequence_problem(self):
        start = random.randint(1, 9)
        step = random.randint(2, 6)
        nums = [start + i * step for i in range(4)]
        missing_idx = random.randint(1, 2)
        answer = nums[missing_idx]
        display = [str(n) if i != missing_idx else '?' for i, n in enumerate(nums)]
        return Captcha(', '.join(display), str(answer))

    def random_color(self):
        return tuple(random.randint(0, 255) for _ in range(3))

    def luminance(self, color):
        return color[0] * RED_FACTOR + color[1] * GREEN_FACTOR + color[2] * BLUE_FACTOR

    def _contrasting_color(self, bg):
        return (0, 0, 0) if self.luminance(bg) > THRESHOLD else (255, 255, 255)

    def _fit_font(self, draw, text, max_width):
        size = config.FONT_SIZE
        while size > 24:
            font = ImageFont.truetype(config.FONT_PATH, size)
            if draw.textlength(text, font) <= max_width - 20:
                return font, size
            size -= 10
        font = ImageFont.truetype(config.FONT_PATH, size)
        return font, size

    def _add_noise(self, draw, noise_color, level=None):
        level = level if level is not None else config.NOISE_LEVEL
        for i in range(config.PIC_WIDTH):
            for j in range(config.PIC_HEIGHT):
                if random.random() < level / 100:
                    draw.point((i, j), fill=noise_color)

    def _render_text_captcha(self, captcha: Captcha, hint_key: str) -> Result:
        bg_color = self.random_color()
        fg_color = self._contrasting_color(bg_color)
        noise_color = self.random_color()

        image = Image.new('RGB', (config.PIC_WIDTH, config.PIC_HEIGHT), bg_color)
        draw = ImageDraw.Draw(image)

        font, font_size = self._fit_font(draw, captcha.problem, config.PIC_WIDTH)
        text_width = draw.textlength(captcha.problem, font)
        x = (config.PIC_WIDTH - text_width) / 2
        y = (config.PIC_HEIGHT - font_size) / 2
        draw.text((x, y), captcha.problem, font=font, fill=fg_color)

        self._add_noise(draw, noise_color)

        _, path = tempfile.mkstemp(suffix='.png')
        image.save(path)
        return Result(path, captcha.solution, hint_key)

    def _render_shapes_captcha(self) -> Result:
        count = random.randint(3, 9)
        bg_color = self.random_color()

        image = Image.new('RGB', (config.PIC_WIDTH, config.PIC_HEIGHT), bg_color)
        draw = ImageDraw.Draw(image)

        radius = 13
        min_dist_sq = (radius * 2 + 8) ** 2
        positions: list[tuple[int, int]] = []
        placed = 0
        attempts = 0

        while placed < count and attempts < 2000:
            x = random.randint(radius + 4, config.PIC_WIDTH - radius - 4)
            y = random.randint(radius + 4, config.PIC_HEIGHT - radius - 4)
            if all((x - px) ** 2 + (y - py) ** 2 > min_dist_sq for px, py in positions):
                positions.append((x, y))
                fill = self.random_color()
                while abs(self.luminance(fill) - self.luminance(bg_color)) < 60:
                    fill = self.random_color()
                outline = self._contrasting_color(fill)
                draw.ellipse(
                    [x - radius, y - radius, x + radius, y + radius],
                    fill=fill,
                    outline=outline,
                    width=2
                )
                placed += 1
            attempts += 1

        self._add_noise(draw, self.random_color(), level=config.NOISE_LEVEL // 3)

        _, path = tempfile.mkstemp(suffix='.png')
        image.save(path)
        return Result(path, str(placed), 'hint_shapes')

    def generate_picture(self) -> Result:
        captcha_type = random.choice(('math', 'text', 'sequence', 'shapes'))

        if captcha_type == 'math':
            return self._render_text_captcha(self.generate_math_problem(), 'hint_math')
        elif captcha_type == 'text':
            return self._render_text_captcha(self.generate_text_problem(), 'hint_text')
        elif captcha_type == 'sequence':
            return self._render_text_captcha(self.generate_sequence_problem(), 'hint_sequence')
        else:
            return self._render_shapes_captcha()
