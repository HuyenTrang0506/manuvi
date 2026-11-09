import pymupdf
import pytest


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                 "AWS_DEFAULT_REGION": "ap-southeast-2"}.items():
        monkeypatch.setenv(k, v)


PAGES = [
    ["Remove connector YT12-AB3C from the relay box.", "Tighten bolt to 12 Nm. See Fig. 3-2.",
     "WARNING: Disconnect the battery before service. Part YN20-01234-A."],
    ["Check wiring harness continuity between pin 3 and pin 7.", "CAUTION: Hot surface.",
     "Reconnect the battery terminal and test at 24 V."],
    ["Install relay box YN20-01234-A and torque to 10 N.m.", "Fuse"],
]


def make_pdf(path, pages=PAGES):
    doc = pymupdf.open()
    for i, texts in enumerate(pages):
        page = doc.new_page()
        y = 72
        for t in texts:
            page.insert_textbox(pymupdf.Rect(72, y, 520, y + 40), t, fontsize=11)
            y += 60
        page.insert_text((280, 820), f"23-{i + 1}", fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf_bytes(tmp_path):  # also leaves tmp_path/in.pdf
    return make_pdf(tmp_path / "in.pdf").read_bytes()
