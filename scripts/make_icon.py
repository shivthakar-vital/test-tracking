"""Draw the app icon and write assets/icon.png + assets/icon.icns (macOS)."""
import os, shutil, subprocess, sys
from PyQt5.QtCore import Qt, QRectF, QPointF
from PyQt5.QtGui import QGuiApplication, QImage, QPainter, QColor, QPen, QPainterPath

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")

def draw(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 1024
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(100 * s, 100 * s, 824 * s, 824 * s), 185 * s, 185 * s)
    p.fillPath(bg, QColor("#1D6F9E"))
    # a donut chart: most of it done, a slice still to go
    ring = QRectF(250 * s, 250 * s, 524 * s, 524 * s)
    pen = QPen(QColor("#FFFFFF"), 110 * s)
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)
    p.drawArc(ring.adjusted(55 * s, 55 * s, -55 * s, -55 * s), 90 * 16, -270 * 16)
    pen.setColor(QColor("#9CC7E6"))
    p.setPen(pen)
    p.drawArc(ring.adjusted(55 * s, 55 * s, -55 * s, -55 * s), 90 * 16 - 275 * 16, -80 * 16)
    # a check mark in the middle
    check = QPen(QColor("#FFFFFF"), 56 * s)
    check.setCapStyle(Qt.RoundCap)
    check.setJoinStyle(Qt.RoundJoin)
    p.setPen(check)
    path = QPainterPath(QPointF(430 * s, 520 * s))
    path.lineTo(495 * s, 585 * s)
    path.lineTo(610 * s, 450 * s)
    p.drawPath(path)
    p.end()
    return img

if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    draw(1024).save(os.path.join(ROOT, "icon.png"))
    if sys.platform == "darwin":
        iconset = os.path.join(ROOT, "icon.iconset")
        os.makedirs(iconset, exist_ok=True)
        for n in (16, 32, 128, 256, 512):
            draw(n).save(os.path.join(iconset, f"icon_{n}x{n}.png"))
            draw(n * 2).save(os.path.join(iconset, f"icon_{n}x{n}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(ROOT, "icon.icns")], check=True)
        shutil.rmtree(iconset)
    print("wrote icon to", os.path.abspath(ROOT))
