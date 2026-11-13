#!/usr/bin/env python3
"""Generate the application icon and Finder installer backgrounds."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)

icon = Image.new('RGBA', (1024, 1024))
draw = ImageDraw.Draw(icon)
draw.rounded_rectangle((24, 24, 1000, 1000), radius=220, fill='#16344d')
draw.rounded_rectangle((195, 220, 829, 750), radius=105, fill='#f6f5ed')
draw.polygon([(330, 720), (330, 845), (490, 720)], fill='#f6f5ed')
for index, width in enumerate((390, 290)):
    draw.rounded_rectangle((290, 400 + index*145, 290+width, 452+index*145), radius=26, fill='#16344d')
icon.save(args.output/'CaptionFlow.icns', format='ICNS')

font = Path('/System/Library/Fonts/Supplemental/Arial.ttf')
bold = Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf')
for architecture, label in [('arm64', 'Apple Silicon'), ('x86_64', 'Intel')]:
    for scale in (1, 2):
        background = Image.new('RGB', (600*scale, 380*scale), '#f5f5f1')
        draw = ImageDraw.Draw(background)
        def text(y, value, size, color, weight=font):
            draw.text((300*scale, y*scale), value, anchor='mt', fill=color,
                      font=ImageFont.truetype(str(weight), size*scale))
        text(30, 'CaptionFlow', 30, '#16344d', bold)
        text(76, 'Drag to Applications to install', 17, '#535b60')
        draw.line([(275*scale, 192*scale), (325*scale, 192*scale)], fill='#7d929c', width=3*scale)
        draw.line([(313*scale, 181*scale), (325*scale, 192*scale), (313*scale, 203*scale)], fill='#7d929c', width=3*scale)
        text(310, f'For {label} Macs', 14, '#535b60')
        text(338, 'Then open CaptionFlow from Applications.', 13, '#697177')
        suffix = '@2x' if scale == 2 else ''
        background.save(args.output/f'installer-{architecture}{suffix}.png')
