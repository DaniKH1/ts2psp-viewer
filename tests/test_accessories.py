"""Accessory metadata checks that do not need a game ISO."""

import io
import unittest
import zipfile

from ts2psp.formats.accessories import accessory_mesh_paths, accessory_names


class AccessoryTests(unittest.TestCase):
    def archive(self, content, mesh=True):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("fichas/npc.res", content)
            if mesh:
                archive.writestr(
                    "mallas/characters/bodyanims/hair/afhairhatmaid.res", b""
                )
        buffer.seek(0)
        return zipfile.ZipFile(buffer)

    def test_old_cache_reads_only_the_matching_species(self):
        content = """
newSpecies {
    bodyHair = "hair/emily"
    accessories_0 = "fx_hatmaid"
}
newSpecies {
    bodyHair = "hair/zina"
    -- accessories_0 = "fx_hatmaid"
}
"""
        with self.archive(content) as archive:
            emily = {"bodyHair": "hair/emily", "archivo_zip": "fichas/npc.res"}
            zina = {"bodyHair": "hair/zina", "archivo_zip": "fichas/npc.res"}
            self.assertEqual(accessory_names(archive, emily), ("fx_hatmaid",))
            self.assertEqual(accessory_names(archive, zina), ())

    def test_csv_slots_take_precedence_and_paths_are_deduplicated(self):
        row = {"accessories_0": "fx_hatmaid", "accessories_1": "fx_hatmaid"}
        with self.archive("") as archive:
            self.assertEqual(accessory_mesh_paths(archive, row), ["hair/afhairhatmaid"])

    def test_missing_and_unsupported_accessories_do_not_break_character_loading(self):
        with self.archive("", mesh=False) as archive:
            for name in ("fx_hatmaid", "fx_unknown"):
                self.assertEqual(
                    accessory_mesh_paths(archive, {"accessories_0": name}), []
                )
