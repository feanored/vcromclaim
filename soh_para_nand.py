#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soh_para_n64_nand.py
====================
Caminho de VOLTA: pega um save do Ship of Harkinian (fileN.sav, JSON) e o
encaixa de volta dentro do arquivo de save do Nintendo 64 que o Wii/Virtual
Console le, preservando o resto do container intacto.

COMO FUNCIONA
-------------
1. Reconstroi o slot de SRAM do Ocarina (0x1450 bytes) a partir do JSON,
   recalculando o checksum interno do jogo.
2. Procura no arquivo container onde o save antigo mora, detectando a ordem
   de bytes (big-endian / 2-byte / 4-byte swapped) ao localizar "ZELDAZ".
3. Substitui SOMENTE os bytes do(s) slot(s) encontrado(s) pelo save novo, na
   mesma ordem de bytes e posicao, deixando todo o resto do arquivo idextico.
4. Re-le o resultado e confere os valores antes de salvar (saude, rupees...).
5. Escreve um arquivo NOVO; nunca sobrescreve o original.

IMPORTANTE - LEIA ANTES
-----------------------
* O container e o arquivo de SAVE, NAO o "banner.bin" (esse e so o icone do
  canal; comeca com "WIBN"). Procure na pasta
      ...\\title\\00010001\\<id do canal>\\data\\
  o outro arquivo (geralmente "data.bin"). E nele que o save N64 fica.
* FACA BACKUP da EmuNAND / do arquivo original antes de trocar qualquer coisa.
* Isso vale para EmuNAND/NEEK (arquivos da NAND "soltos" no SD). Numa NAND
  REAL de Wii (criptografada/ECC) nao se troca o arquivo na mao: use o
  Savegame Manager GX para restaurar.
* Se o container tiver um checksum/assinatura PROPRIO sobre o save (alem do
  checksum interno do Ocarina), um splice simples pode nao bastar. O script
  avisa o que encontrou; se desconfiar, confira o arquivo original primeiro.

USO
---
  python soh_para_n64_nand.py file1.sav data.bin
  python soh_para_n64_nand.py file1.sav data.bin --out data_novo.bin
  python soh_para_n64_nand.py file1.sav data.bin --sra-out save.sra

Tambem da para so gerar a SRAM solta (sem container):
  python soh_para_n64_nand.py file1.sav --sra-out save.sra
"""

import argparse
import struct
import json
import sys

SLOT_SIZE = 0x1450
MAGIC = b"ZELDAZ"
MAGIC_OFF = 0x1C
CHECKSUM_OFF = 0x1352


# ===========================================================================
# Reconstrucao do slot de SRAM a partir do JSON do SoH (inverso do parser)
# ===========================================================================
class _W:
    def __init__(self, buf=None):
        self.s = bytearray(buf) if buf is not None else bytearray(SLOT_SIZE)
    def u8(self, o, v):  self.s[o] = v & 0xFF
    def i8(self, o, v):  struct.pack_into(">b", self.s, o, max(-128, min(127, int(v))))
    def u16(self, o, v): struct.pack_into(">H", self.s, o, v & 0xFFFF)
    def s16(self, o, v): struct.pack_into(">h", self.s, o, int(v))
    def u32(self, o, v): struct.pack_into(">I", self.s, o, v & 0xFFFFFFFF)
    def s32(self, o, v): struct.pack_into(">i", self.s, o, int(v))


def _w_itemequips(w, o, d):
    for i in range(4):
        w.u8(o + 0x00 + i, d["buttonItems"][i])
    for i in range(3):
        w.u8(o + 0x04 + i, d["cButtonSlots"][i])
    w.u16(o + 0x08, d["equipment"])


def _w_inventory(w, o, d):
    for i in range(24):
        w.u8(o + 0x00 + i, d["items"][i])
    for i in range(16):
        w.i8(o + 0x18 + i, d["ammo"][i])
    w.u16(o + 0x28, d["equipment"])
    w.u32(o + 0x2C, d["upgrades"])
    w.u32(o + 0x30, d["questItems"])
    for i in range(20):
        w.u8(o + 0x34 + i, d["dungeonItems"][i])
    for i in range(19):
        w.i8(o + 0x48 + i, d["dungeonKeys"][i])
    w.i8(o + 0x5B, d["defenseHearts"])
    w.s16(o + 0x5C, d["gsTokens"])


def _w_sceneflag(w, o, d):
    for k, off in (("chest", 0), ("swch", 4), ("clear", 8), ("collect", 0xC),
                   ("unk", 0x10), ("rooms", 0x14), ("floors", 0x18)):
        w.u32(o + off, d[k])


def _w_fw(w, o, d):
    w.s32(o + 0x00, d["pos"]["x"]); w.s32(o + 0x04, d["pos"]["y"]); w.s32(o + 0x08, d["pos"]["z"])
    w.s32(o + 0x0C, d["yaw"]); w.s32(o + 0x10, d["playerParams"])
    w.s32(o + 0x14, d["entranceIndex"]); w.s32(o + 0x18, d["roomIndex"])
    w.s32(o + 0x1C, d["set"]); w.s32(o + 0x20, d["tempSwchFlags"]); w.s32(o + 0x24, d["tempCollectFlags"])


def _w_ocnote(w, o, d):
    for i, k in enumerate(("noteIdx", "unk_01", "unk_02", "volume", "vibrato", "tone", "semitone")):
        w.u8(o + i, d[k])


def build_slot_from_json(base, base_slot=None) -> bytes:
    """Monta os 0x1450 bytes do slot (big-endian). base_slot preserva bytes nao mapeados."""
    w = _W(base_slot)
    w.s32(0x00, base["entranceIndex"]); w.s32(0x04, base["linkAge"]); w.s32(0x08, base["cutsceneIndex"])
    w.u16(0x0C, base["dayTime"]); w.s32(0x10, base["nightFlag"]); w.s32(0x14, base["totalDays"])
    w.s32(0x18, base["bgsDayCount"])
    w.s[MAGIC_OFF:MAGIC_OFF + 6] = MAGIC
    w.u16(0x22, base["deaths"])
    for i in range(8):
        w.u8(0x24 + i, base["playerName"][i])
    w.s16(0x2E, base["healthCapacity"]); w.s16(0x30, base["health"])
    w.i8(0x32, base["magicLevel"]); w.i8(0x33, base["magic"])
    w.s16(0x34, base["rupees"]); w.u16(0x36, base["swordHealth"]); w.u16(0x38, base["naviTimer"])
    w.u8(0x3A, base["isMagicAcquired"]); w.u8(0x3C, base["isDoubleMagicAcquired"])
    w.u8(0x3D, base["isDoubleDefenseAcquired"]); w.u8(0x3E, base["bgsFlag"])
    w.u8(0x3F, base["ocarinaGameRoundNum"])
    _w_itemequips(w, 0x40, base["childEquips"]); _w_itemequips(w, 0x4A, base["adultEquips"])
    w.u32(0x54, base["unk_54"]); w.s16(0x66, base["savedSceneNum"])
    _w_itemequips(w, 0x68, base["equips"]); _w_inventory(w, 0x74, base["inventory"])
    for i in range(124):
        _w_sceneflag(w, 0xD4 + i * 0x1C, base["sceneFlags"][i])
    _w_fw(w, 0xE64, base["fw"])
    for i in range(6):
        w.s32(0xE9C + i * 4, base["gsFlags"][i])
    for i in range(7):
        w.s32(0xEB8 + i * 4, base["highScores"][i])
    for i in range(14):
        w.u16(0xED4 + i * 2, base["eventChkInf"][i])
    for i in range(4):
        w.u16(0xEF0 + i * 2, base["itemGetInf"][i])
    for i in range(30):
        w.u16(0xEF8 + i * 2, base["infTable"][i])
    w.u32(0xF38, base["worldMapAreaData"]); w.u8(0xF40, base["scarecrowLongSongSet"])
    for i in range(108):
        _w_ocnote(w, 0xF41 + i * 8, base["scarecrowLongSong"][i])
    w.u8(0x12C5, base["scarecrowSpawnSongSet"])
    for i in range(16):
        _w_ocnote(w, 0x12C6 + i * 8, base["scarecrowSpawnSong"][i])
    hd = base["horseData"]
    w.s16(0x1348, hd["scene"]); w.s16(0x134A, hd["pos"]["x"]); w.s16(0x134C, hd["pos"]["y"])
    w.s16(0x134E, hd["pos"]["z"]); w.s16(0x1350, hd["angle"])
    # checksum interno do Ocarina: soma de u16 BE em [0x00, 0x1352)
    csum = sum(struct.unpack_from(">H", w.s, o)[0] for o in range(0, CHECKSUM_OFF, 2)) & 0xFFFF
    w.u16(CHECKSUM_OFF, csum)
    return bytes(w.s)


# Leitura minima do slot, so para verificacao
def _read_check(slot: bytes) -> dict:
    return {
        "healthCapacity": struct.unpack_from(">h", slot, 0x2E)[0],
        "health":         struct.unpack_from(">h", slot, 0x30)[0],
        "magic":          struct.unpack_from(">b", slot, 0x33)[0],
        "rupees":         struct.unpack_from(">h", slot, 0x34)[0],
        "linkAge":        struct.unpack_from(">i", slot, 0x04)[0],
        "savedSceneNum":  struct.unpack_from(">h", slot, 0x66)[0],
    }


# ===========================================================================
# Ordem de bytes e localizacao dos slots no container
# ===========================================================================
def _swap2(b):
    o = bytearray(len(b))
    n = len(b) - (len(b) % 2)
    for i in range(0, n, 2):
        o[i], o[i + 1] = b[i + 1], b[i]
    if n != len(b):
        o[n] = b[n]
    return bytes(o)


def _swap4(b):
    o = bytearray(len(b))
    n = len(b) - (len(b) % 4)
    for i in range(0, n, 4):
        o[i:i + 4] = b[i:i + 4][::-1]
    o[n:] = b[n:]
    return bytes(o)


# Cada transform e seu proprio inverso (swap2/swap4) ou identidade.
TRANSFORMS = [("big-endian (nativo N64)", lambda b: b),
              ("4-byte word-swapped", _swap4),
              ("2-byte byte-swapped", _swap2)]


def locate(container: bytes):
    """Retorna (label, transform, view_big_endian, [posicoes_de_ZELDAZ]) ou (None,...)."""
    for label, T in TRANSFORMS:
        view = T(container)
        pos = []
        idx = 0
        while True:
            p = view.find(MAGIC, idx)
            if p < 0:
                break
            pos.append(p)
            idx = p + 1
        if pos:
            return label, T, view, pos
    return None, None, None, []


# ===========================================================================
# Principal
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Encaixa um save do Ship of Harkinian (fileN.sav) de volta "
                    "no arquivo de save N64 que o Wii/VC le.")
    ap.add_argument("soh", help="arquivo fileN.sav (JSON) do Ship of Harkinian")
    ap.add_argument("container", nargs="?",
                    help="arquivo de save do Wii (ex.: data.bin). Omita para so gerar a .sra")
    ap.add_argument("--out", help="arquivo container de saida (padrao: <container>.novo)")
    ap.add_argument("--sra-out", help="tambem grava a SRAM solta de 32KB neste caminho")
    ap.add_argument("--slot-index", type=int, default=None,
                    help="se houver varios saves, indice (0,1,...) do slot a substituir; "
                         "padrao: substitui todos os encontrados")
    args = ap.parse_args(argv)

    # 1) JSON -> slot big-endian
    j = json.load(open(args.soh, encoding="utf-8"))
    try:
        base = j["sections"]["base"]["data"]
    except (KeyError, TypeError):
        print("ERRO: isso nao parece um fileN.sav valido do SoH (sem sections.base.data).",
              file=sys.stderr)
        return 2
    slot_be = build_slot_from_json(base)
    chk = _read_check(slot_be)
    print("Save reconstruido do JSON:")
    print(f"  {'Crianca' if chk['linkAge']==1 else 'Adulto'} | "
          f"{chk['healthCapacity']//16} coracoes (health={chk['health']}) | "
          f"magia={chk['magic']} | rupees={chk['rupees']} | cena={chk['savedSceneNum']}")

    # 1b) SRAM solta opcional (32KB com o save no slot 3 + backup, layout padrao)
    if args.sra_out:
        sram = bytearray(0x8000)
        sram[0x28C0:0x28C0 + SLOT_SIZE] = slot_be   # File 3
        sram[0x65B0:0x65B0 + SLOT_SIZE] = slot_be   # backup File 3
        open(args.sra_out, "wb").write(sram)
        print(f"SRAM solta gravada em: {args.sra_out}")

    if not args.container:
        if not args.sra_out:
            print("Nada a fazer: informe um container ou use --sra-out.", file=sys.stderr)
            return 2
        return 0

    # 2) Localiza no container
    container = bytearray(open(args.container, "rb").read())
    if container[:4] == b"WIBN":
        print("ERRO: este arquivo e o BANNER (icone), nao o save. Procure o outro "
              "arquivo (ex.: data.bin) na pasta data\\ do canal.", file=sys.stderr)
        return 2

    label, T, view, positions = locate(bytes(container))
    if not positions:
        print("ERRO: nao achei 'ZELDAZ' no container em nenhuma ordem de bytes. "
              "Este arquivo contem mesmo o save do Ocarina?", file=sys.stderr)
        return 2

    slot_starts = sorted({p - MAGIC_OFF for p in positions if p - MAGIC_OFF >= 0})
    print(f"Ordem de bytes do container: {label}")
    print(f"Save(s) encontrados em: {[hex(s) for s in slot_starts]}")

    # 3) Splice na view big-endian, depois reverte a transform
    view = bytearray(view)
    targets = slot_starts
    if args.slot_index is not None:
        if not (0 <= args.slot_index < len(slot_starts)):
            print(f"ERRO: --slot-index fora do intervalo (0..{len(slot_starts)-1}).", file=sys.stderr)
            return 2
        targets = [slot_starts[args.slot_index]]

    for st in targets:
        if st + SLOT_SIZE > len(view):
            print(f"AVISO: slot em {hex(st)} ultrapassa o fim do arquivo; pulando.")
            continue
        view[st:st + SLOT_SIZE] = slot_be
    new_container = T(bytes(view))  # T e involucao -> reverte a ordem original

    # 4) Verificacao: re-localiza e confere os valores gravados
    vlabel, vT, vview, vpos = locate(new_container)
    ok = False
    if vpos:
        for st in [p - MAGIC_OFF for p in vpos]:
            got = _read_check(bytes(vview)[st:st + SLOT_SIZE])
            if got == chk:
                ok = True
                break
    if not ok:
        print("ERRO: verificacao falhou apos o splice. NAO use o arquivo gerado.",
              file=sys.stderr)
        return 3
    if len(new_container) != len(container):
        print("ERRO: tamanho do arquivo mudou; abortando.", file=sys.stderr)
        return 3

    # 5) Grava arquivo NOVO (nunca sobrescreve o original)
    out = args.out or (args.container + ".novo")
    open(out, "wb").write(new_container)
    print("-" * 56)
    print(f"OK! Container gerado: {out}")
    print("  (verificado: os valores do save batem com o JSON)")
    print("Passos finais:")
    print("  1) FACA BACKUP do arquivo original na EmuNAND.")
    print(f"  2) Renomeie '{out}' para o nome original e ponha no lugar.")
    print("  3) Se o canal nao reconhecer o save, o container provavelmente tem")
    print("     um checksum proprio - me mostre o original que eu investigo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
