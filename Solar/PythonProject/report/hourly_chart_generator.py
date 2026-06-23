import os
from xml.sax.saxutils import escape

from config import OUTPUT_DIR
from utils.logger import get_logger

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

logger = get_logger("HourlyChartGenerator")


class HourlyChartGenerator:
    def generate_hourly_chart_image(self, dt, total_users, present_counts, missing_counts):
        # Prefer PNG for consistent rendering across chat clients (including Google Chat).
        if PIL_AVAILABLE:
            path = self._generate_hourly_chart_png(dt, total_users, present_counts, missing_counts)
            if path:
                return path, "image/png"
        # Fallback to SVG if PIL is unavailable.
        svg_path = self._generate_hourly_chart_svg(dt, total_users, present_counts, missing_counts)
        if svg_path:
            return svg_path, "image/svg+xml"
        return None, None

    def _generate_hourly_chart_png(self, dt, total_users, present_counts, missing_counts):
        """Generate a high-resolution PNG and downscale for crisper text and shapes.

        Approach:
        - Draw at 2x (SCALE) resolution with larger fonts and thicker strokes,
          then downsample using LANCZOS for high-quality antialiasing.
        - This helps when chat clients resize images and prevents blurred text/axes.
        """
        date_dir = os.path.join(OUTPUT_DIR, dt)
        os.makedirs(date_dir, exist_ok=True)
        output_path = os.path.join(date_dir, "hourly_present_absent.png")

        BASE_W = 1200
        BASE_H = 820
        SCALE = 2
        width = BASE_W * SCALE
        height = BASE_H * SCALE

        margin_left = 155 * SCALE
        margin_right = 45 * SCALE
        margin_top = 130 * SCALE
        margin_bottom = 165 * SCALE
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        max_count = max(1, total_users)

        present_color = (46, 125, 50)
        missing_color = (198, 40, 40)
        axis_color = (38, 50, 56)
        grid_color = (218, 224, 230)
        bg_color = (255, 255, 255, 255)

        img = Image.new("RGBA", (width, height), bg_color)
        draw = ImageDraw.Draw(img)

        def load_font(font_name, size):
            for candidate in (
                font_name,
                f"/usr/share/fonts/truetype/dejavu/{font_name}",
                f"/Library/Fonts/{font_name}",
                f"/System/Library/Fonts/Supplemental/{font_name}",
            ):
                try:
                    return ImageFont.truetype(candidate, size)
                except Exception:
                    continue
            logger.warning(f"Could not load {font_name}; using default PIL font")
            return ImageFont.load_default()

        title_font = load_font("DejaVuSans-Bold.ttf", int(34 * SCALE))
        label_font = load_font("DejaVuSans-Bold.ttf", int(42 * SCALE))
        tick_font = load_font("DejaVuSans-Bold.ttf", int(28 * SCALE))

        def y_for(value):
            return margin_top + plot_h - int(round((value / max_count) * plot_h))

        def draw_text_centered(x, y, text, font, fill):
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((x - tw / 2, y - th / 2), text, font=font, fill=fill)

        # Horizontal grid lines and Y ticks
        for i in range(6):
            tick_val = int(round((max_count * i) / 5))
            y = y_for(tick_val)
            draw.line([(margin_left, y), (margin_left + plot_w, y)], fill=grid_color, width=2 * SCALE)
            # Draw tick label right-aligned
            bbox = draw.textbbox((0, 0), str(tick_val), font=tick_font)
            th = bbox[3] - bbox[1]
            draw.text((margin_left - 32 * SCALE, y - th / 2), str(tick_val), font=tick_font, fill=axis_color)

        # Axes (thicker)
        draw.line([(margin_left, margin_top + plot_h), (margin_left + plot_w, margin_top + plot_h)], fill=axis_color, width=6 * SCALE)
        draw.line([(margin_left, margin_top), (margin_left, margin_top + plot_h)], fill=axis_color, width=6 * SCALE)

        group_w = plot_w / 24.0
        bar_w = group_w * 0.62

        # Bars
        for hour in range(24):
            present = max(0, present_counts[hour] if hour < len(present_counts) else 0)
            missing = max(0, missing_counts[hour] if hour < len(missing_counts) else 0)

            x_base = margin_left + hour * group_w
            x_bar = x_base + (group_w - bar_w) / 2

            y_present_top = y_for(present)
            y_total_top = y_for(present + missing)

            draw.rectangle([(x_bar, y_present_top), (x_bar + bar_w, margin_top + plot_h)], fill=present_color, outline=None)
            draw.rectangle([(x_bar, y_total_top), (x_bar + bar_w, y_present_top)], fill=missing_color, outline=None)

            if hour % 2 == 0:
                draw_text_centered(int(x_base + group_w / 2), margin_top + plot_h + int(34 * SCALE), f"{hour:02d}", tick_font, axis_color)

        # Axis labels
        draw_text_centered(width // 2, margin_top + plot_h + int(105 * SCALE), "X-axis: Hour", label_font, axis_color)
        draw.text((margin_left, margin_top - int(88 * SCALE)), "Y-axis: User Count", font=label_font, fill=axis_color)

        # Legend
        legend_x = width - int(350 * SCALE / 1)
        legend_y = int(46 * SCALE / 1)
        draw.rectangle([(legend_x, legend_y), (legend_x + 18 * SCALE, legend_y + 18 * SCALE)], fill=present_color)
        draw.text((legend_x + 26 * SCALE, legend_y - 2 * SCALE), "Present", font=tick_font, fill=axis_color)
        draw.rectangle([(legend_x + 138 * SCALE, legend_y), (legend_x + 156 * SCALE, legend_y + 18 * SCALE)], fill=missing_color)
        draw.text((legend_x + 164 * SCALE, legend_y - 2 * SCALE), "Absent", font=tick_font, fill=axis_color)

        try:
            # Downscale to base size for crisper rendering when displayed at smaller sizes
            final = img.resize((BASE_W, BASE_H), resample=Image.LANCZOS)
            # Convert to RGB before saving to PNG to avoid potential alpha issues
            final_rgb = final.convert("RGB")
            final_rgb.save(output_path, format="PNG", optimize=True)
            return output_path
        except Exception as exc:
            logger.error(f"Failed to generate chart PNG for {dt}: {exc}")
            return None

    def _generate_hourly_chart_svg(self, dt, total_users, present_counts, missing_counts):
        date_dir = os.path.join(OUTPUT_DIR, dt)
        os.makedirs(date_dir, exist_ok=True)
        output_path = os.path.join(date_dir, "hourly_present_absent.svg")

        width = 960
        height = 760
        margin_left = 100
        margin_right = 30
        margin_top = 90
        margin_bottom = 120
        plot_w = width - margin_left - margin_right
        plot_h = height - margin_top - margin_bottom
        max_count = max(1, total_users)

        def y_for(value):
            return margin_top + plot_h - (value / max_count) * plot_h

        group_w = plot_w / 24.0
        bar_w = group_w * 0.62
        present_color = "#2E7D32"
        missing_color = "#C62828"
        axis_color = "#263238"
        grid_color = "#CFD8DC"

        lines = [
            f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
            "<rect width='100%' height='100%' fill='#FFFFFF'/>",
        ]

        for i in range(6):
            tick_val = int(round((max_count * i) / 5))
            y = y_for(tick_val)
            lines.append(
                f"<line x1='{margin_left}' y1='{y:.2f}' x2='{margin_left + plot_w}' y2='{y:.2f}' stroke='{grid_color}' stroke-width='2'/>"
            )
            lines.append(
                f"<text x='{margin_left - 14}' y='{y + 6:.2f}' text-anchor='end' font-size='14' font-family='Arial' fill='{axis_color}'>{tick_val}</text>"
            )

        lines.append(
            f"<line x1='{margin_left}' y1='{margin_top + plot_h}' x2='{margin_left + plot_w}' y2='{margin_top + plot_h}' stroke='{axis_color}' stroke-width='2'/>"
        )
        lines.append(
            f"<line x1='{margin_left}' y1='{margin_top}' x2='{margin_left}' y2='{margin_top + plot_h}' stroke='{axis_color}' stroke-width='2'/>"
        )
        lines.append(
            f"<text x='{margin_left + plot_w/2}' y='{height - 30}' text-anchor='middle' font-size='20' font-family='Arial' fill='{axis_color}'>X-axis: Hour</text>"
        )
        lines.append(
            f"<text x='{margin_left - 88}' y='{margin_top - 16}' text-anchor='start' font-size='20' font-family='Arial' fill='{axis_color}'>Y-axis: User Count</text>"
        )

        for hour in range(24):
            present = max(0, present_counts[hour] if hour < len(present_counts) else 0)
            missing = max(0, missing_counts[hour] if hour < len(missing_counts) else 0)

            x_base = margin_left + hour * group_w
            x_bar = x_base + (group_w - bar_w) / 2

            y_present_top = y_for(present)
            y_total_top = y_for(present + missing)
            h_present = (margin_top + plot_h) - y_present_top
            h_missing = y_present_top - y_total_top

            lines.append(
                f"<rect x='{x_bar:.2f}' y='{y_present_top:.2f}' width='{bar_w:.2f}' height='{h_present:.2f}' fill='{present_color}'/>"
            )
            lines.append(
                f"<rect x='{x_bar:.2f}' y='{y_total_top:.2f}' width='{bar_w:.2f}' height='{h_missing:.2f}' fill='{missing_color}'/>"
            )
            if hour % 2 == 0:
                x_label = x_base + group_w / 2
                lines.append(
                    f"<text x='{x_label:.2f}' y='{margin_top + plot_h + 20}' text-anchor='middle' font-size='12' font-family='Arial' fill='{axis_color}'>{hour:02d}</text>"
                )

        legend_x = margin_left + plot_w - 230
        legend_y = margin_top - 44
        lines.append(f"<rect x='{legend_x}' y='{legend_y}' width='14' height='14' fill='{present_color}'/>")
        lines.append(
            f"<text x='{legend_x + 20}' y='{legend_y + 12}' font-size='16' font-family='Arial' fill='{axis_color}'>Present</text>"
        )
        lines.append(
            f"<rect x='{legend_x + 110}' y='{legend_y}' width='14' height='14' fill='{missing_color}'/>"
        )
        lines.append(
            f"<text x='{legend_x + 130}' y='{legend_y + 12}' font-size='16' font-family='Arial' fill='{axis_color}'>Absent</text>"
        )

        lines.append("</svg>")

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return output_path
        except Exception as exc:
            logger.error(f"Failed to generate chart SVG for {dt}: {exc}")
            return None
