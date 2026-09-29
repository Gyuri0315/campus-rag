import base64
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image
from scripts.crawlers.departments.body_images import collect_body_images, save_body_images, image_info


def png():
    output = BytesIO()
    Image.new('RGB', (40, 50), 'white').save(output, format='PNG')
    return output.getvalue()


class BodyImageTests(unittest.TestCase):
    def test_svg_original_validation(self):
        self.assertEqual(image_info(b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="50"/>')[:2], ('svg', 'image/svg+xml'))
        with self.assertRaises(ValueError):
            image_info(b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "test">]><svg/>')

    def test_body_scope_decorations_and_lazy_images(self):
        soup = BeautifulSoup('''<header><img src="outside.png"></header><div class="bdvEdit">
        <nav><img src="menu.png"></nav><div class="share"><img src="x.png"></div>
        <img src="/images/logo_school.png"><img src="icon.gif"><img src="spacer.gif">
        <img width="1" height="1" src="pixel.gif"><img src="poster.png" alt="공지">
        <img src="placeholder.gif" data-src="real.jpg"></div><footer><img src="foot.png"></footer>''', 'lxml')
        records = collect_body_images(soup)
        self.assertEqual([r['src'] for r in records], ['poster.png', 'real.jpg'])
        self.assertEqual([r['order'] for r in records], [1, 2])

    def test_no_body_does_not_collect_layout(self):
        self.assertEqual(collect_body_images(BeautifulSoup('<body><img src="a.png"></body>', 'lxml')), [])

    def test_inline_and_remote_share_original_hash(self):
        payload = png()
        data = 'data:image/png;base64,' + base64.b64encode(payload).decode()
        response = SimpleNamespace(url='https://example.org/image.png', raise_for_status=lambda:None,
                                   iter_content=lambda size:iter([payload]), close=lambda:None)
        with tempfile.TemporaryDirectory() as temp, patch('scripts.crawlers.departments.body_images.get_with_tls_policy', return_value=(response, False)) as fetch:
            root = Path(temp)
            records = save_body_images([{'order':1,'src':data,'alt':''}, {'order':2,'src':'image.png','alt':''},
                                       {'order':3,'src':'image.png','alt':''}, {'order':4,'src':'data:image/png;base64,bad!','alt':''}],
                session=object(),page_url='https://example.org/page',output_dir=root/'images',project_root=root)
            self.assertEqual([r['status'] for r in records], ['saved','saved','saved','failed'])
            self.assertEqual(records[0]['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(records[0]['saved_path'], records[1]['saved_path'])
            self.assertEqual((root/records[0]['saved_path']).read_bytes(),payload)
            self.assertEqual(len(list((root/'images').iterdir())),1)
            self.assertEqual(fetch.call_count,1)
            self.assertIsNone(records[0]['source_url'])
            self.assertEqual(records[1]['source_url'],'https://example.org/image.png')

    def test_html_response_rejected_and_no_download_honored(self):
        response=SimpleNamespace(url='https://example.org/a.png', raise_for_status=lambda:None,
            iter_content=lambda size:iter([b'<html>login</html>']), close=lambda:None)
        with tempfile.TemporaryDirectory() as temp, patch('scripts.crawlers.departments.body_images.get_with_tls_policy', return_value=(response,False)) as fetch:
            args=dict(session=object(),page_url='https://example.org',output_dir=Path(temp)/'images',project_root=Path(temp))
            items=[{'order':1,'src':'/a.png','alt':''}]
            records=save_body_images(items,**args,download=False)
            fetch.assert_not_called()
            self.assertEqual(records[0]['status'],'not_attempted')
            self.assertEqual(save_body_images(items,**args)[0]['status'],'failed')
            self.assertFalse((Path(temp)/'images').exists())


if __name__ == '__main__':
    unittest.main()
