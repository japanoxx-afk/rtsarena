"""설치 파이프라인 테스트: 다운로드(file://) → 압축 해제(한글 CP949 이름) → 게임 폴더 탐색.

    python -m unittest discover tests
"""
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena_core.installer import InstallError, extract, find_dir_with, plan  # noqa: E402
from arena_core.net import download, is_synology_share  # noqa: E402
from arena_core.updater import version_key  # noqa: E402

REQ = ["Launcher.exe", "Rhakmu.exe"]


class _Cp949Info(zipfile.ZipInfo):
    """윈도우 압축 도구처럼 UTF-8 플래그 없이 CP949 바이트로 이름을 저장."""

    def _encodeFilenameFlags(self):
        return self.filename.encode("cp437"), self.flag_bits & ~0x800


def cp949_info(name):
    return _Cp949Info(name.encode("cp949").decode("cp437"))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wrapped_korean_zip(self):
        src = self.root / "setup.zip"
        with zipfile.ZipFile(src, "w") as z:
            for n, d in [("라크무/Launcher.exe", b"MZ1"), ("라크무/Rhakmu.exe", b"MZ2"),
                         ("라크무/DATA/맵/테스트.rkm", b"x" * 5000)]:
                z.writestr(cp949_info(n), d)
        got = []
        zip_path = download(src.as_uri(), self.root / "dl" / "rhakmu_setup.zip",
                            lambda d, t, m: got.append(d), expect_zip=True)
        downloads = self.root / "Downloads"
        target, top, _ = plan(zip_path, downloads, "RhakMu")
        self.assertEqual((target, top), (downloads, "라크무"))
        extract(zip_path, target)
        self.assertEqual(find_dir_with(target / top, REQ), (downloads / "라크무").resolve())
        self.assertTrue((downloads / "라크무" / "DATA" / "맵" / "테스트.rkm").is_file())

    def test_flat_zip_gets_own_folder(self):
        src = self.root / "flat.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("Launcher.exe", b"1")
            z.writestr("Rhakmu.exe", b"2")
            z.writestr("Data/a.txt", b"3")
        target, top, _ = plan(src, self.root / "Downloads", "RhakMu")
        self.assertEqual((target, top), (self.root / "Downloads" / "RhakMu", None))
        extract(src, target)
        self.assertIsNotNone(find_dir_with(target, REQ))

    def test_zip_slip_blocked(self):
        src = self.root / "evil.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("../evil.txt", b"x")
        with self.assertRaises(InstallError):
            extract(src, self.root / "out")
        self.assertFalse((self.root / "evil.txt").exists())

    def test_non_zip_rejected(self):
        src = self.root / "page.html"
        src.write_text("<html>not found</html>")
        with self.assertRaises(Exception):
            download(src.as_uri(), self.root / "x.zip", expect_zip=True)

    def test_synology_session_filename(self):
        from arena_core.net import _find_filename
        sample = 'SYNO.SDS.Session = {\n   "enable_logo" : false,\n   "filename" : "RhakMu.zip",\n   "footer_msg" : ""\n};'
        self.assertEqual(_find_filename(sample), "RhakMu.zip")
        self.assertIsNone(_find_filename("<html>no name</html>"))

    def test_helpers(self):
        self.assertTrue(is_synology_share("https://akansas.synology.me:5001/sharing/TQcoAipwf"))
        self.assertFalse(is_synology_share("https://example.com/files/setup.zip"))
        self.assertGreater(version_key("0.9115"), version_key("0.9033"))
        self.assertGreater(version_key("0.10"), version_key("0.9"))


if __name__ == "__main__":
    unittest.main()
