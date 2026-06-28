#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oot_n64_para_soh.py
===================
Converte um save de Ocarina of Time do Nintendo 64 (SRAM bruta, .sra/.srm,
incluindo saves extraidos da Virtual Console do Wii) para o formato JSON
do Ship of Harkinian (fileN.sav das versoes modernas, ex.: 9.x "Ackbar").

POR QUE ESTE SCRIPT EXISTE
--------------------------
O ZeldaSaveTool so gera o formato BINARIO antigo do SoH (oot_save.sav). As
versoes novas do SoH salvam em JSON (file1.sav / file2.sav / file3.sav), com
uma estrutura completamente diferente. Este script faz a ponte, mapeando cada
campo do SaveContext (a partir do codigo-fonte do SoH) para a chave JSON certa.

Resolve tres armadilhas comuns:
  1. ORDEM DE BYTES: detecta sozinho se o .sra esta big-endian, 2-byte ou
     4-byte swapped, e normaliza para big-endian.
  2. SLOT: o save pode estar no File 2 ou File 3, nao no File 1. O script
     varre os 3 slots e acha onde o save realmente esta (procurando "ZELDAZ").
  3. HEALTH ZERADO: saves da VC as vezes vem com health=0; por padrao o script
     enche a vida ate a capacidade para voce nao carregar morto (--no-heal
     desativa).

USO
---
  # mais simples (gera file1.sav usando um molde da sua versao do SoH):
  python oot_n64_para_soh.py meu_save.sra --template file1.sav

  # escolher slot de origem, arquivo de saida e numero do arquivo:
  python oot_n64_para_soh.py meu_save.sra --template file1.sav \
         --slot 3 --out file2.sav --filenum 1

  # sem molde (gera estrutura do zero; menos testado entre versoes):
  python oot_n64_para_soh.py meu_save.sra --out file1.sav

O QUE E O --template
--------------------
Um fileN.sav qualquer gerado pela SUA versao do SoH (basta criar um save novo
no jogo e fechar). Ele e usado como molde para preservar campos que so existem
no SoH (estatisticas, dados de randomizer, idioma do nome, etc.) e garantir
compatibilidade exata com a sua versao. E o caminho mais seguro.

OBS: a entrada deve ser a SRAM BRUTA do N64 (.sra/.srm). Nao use o oot_save.sav
processado pelo ZeldaSaveTool como entrada — ele aplica trocas de bytes em
campos especificos e a leitura sairia errada.
"""

import argparse
import json
import struct
import sys

# ---------------------------------------------------------------------------
# Layout do save do Ocarina of Time (N64)
# ---------------------------------------------------------------------------
SLOT_SIZE = 0x1450          # tamanho de cada slot dentro da SRAM
MAGIC = b"ZELDAZ"           # assinatura no offset +0x1C de cada slot
MAGIC_OFF = 0x1C

# Offsets dos slots PRIMARIOS dentro de uma SRAM de 0x8000 bytes
PRIMARY_SLOTS = {1: 0x0020, 2: 0x1470, 3: 0x28C0}


# ---------------------------------------------------------------------------
# Normalizacao de ordem de bytes
# ---------------------------------------------------------------------------
def _swap2(b: bytes) -> bytes:
    out = bytearray(len(b))
    for i in range(0, len(b) - 1, 2):
        out[i], out[i + 1] = b[i + 1], b[i]
    return bytes(out)


def _swap4(b: bytes) -> bytes:
    out = bytearray(len(b))
    for i in range(0, len(b) - 3, 4):
        out[i:i + 4] = b[i:i + 4][::-1]
    return bytes(out)


def normalize_to_big_endian(data: bytes):
    """Retorna (dados_big_endian, descricao_da_ordem_detectada)."""
    candidates = [
        ("big-endian (nativo N64)", data),
        ("4-byte word-swapped", _swap4(data)),
        ("2-byte byte-swapped", _swap2(data)),
    ]
    for label, cand in candidates:
        for off in PRIMARY_SLOTS.values():
            pos = off + MAGIC_OFF
            if cand[pos:pos + len(MAGIC)] == MAGIC:
                return cand, label
    raise ValueError(
        "Nao encontrei a assinatura 'ZELDAZ' em nenhuma ordem de bytes. "
        "O arquivo nao parece ser uma SRAM valida de Ocarina of Time."
    )


def find_slots_with_save(data: bytes):
    """Lista os numeros de slot (1..3) que contem um save valido."""
    found = []
    for num, off in PRIMARY_SLOTS.items():
        pos = off + MAGIC_OFF
        if data[pos:pos + len(MAGIC)] == MAGIC:
            found.append(num)
    return found


# ---------------------------------------------------------------------------
# Leitura tipada (big-endian) relativa ao inicio do slot
# ---------------------------------------------------------------------------
class SlotReader:
    def __init__(self, slot_bytes: bytes):
        self.s = slot_bytes

    def u8(self, o):  return self.s[o]
    def s8(self, o):  return struct.unpack_from(">b", self.s, o)[0]
    def u16(self, o): return struct.unpack_from(">H", self.s, o)[0]
    def s16(self, o): return struct.unpack_from(">h", self.s, o)[0]
    def u32(self, o): return struct.unpack_from(">I", self.s, o)[0]
    def s32(self, o): return struct.unpack_from(">i", self.s, o)[0]

    def arr(self, fn, o, n, step):
        return [fn(o + i * step) for i in range(n)]


def parse_item_equips(r: SlotReader, o: int) -> dict:
    # ItemEquips original do N64: buttonItems[4], cButtonSlots[3], equipment(u16)
    return {
        "buttonItems":  r.arr(r.u8, o + 0x00, 4, 1),
        "cButtonSlots": r.arr(r.u8, o + 0x04, 3, 1),
        "equipment":    r.u16(o + 0x08),
    }


def parse_inventory(r: SlotReader, o: int) -> dict:
    return {
        "items":         r.arr(r.u8, o + 0x00, 24, 1),
        "ammo":          r.arr(r.s8, o + 0x18, 16, 1),
        "equipment":     r.u16(o + 0x28),
        "upgrades":      r.u32(o + 0x2C),
        "questItems":    r.u32(o + 0x30),
        "dungeonItems":  r.arr(r.u8, o + 0x34, 20, 1),
        "dungeonKeys":   r.arr(r.s8, o + 0x48, 19, 1),
        "defenseHearts": r.s8(o + 0x5B),
        "gsTokens":      r.s16(o + 0x5C),
    }


def parse_scene_flag(r: SlotReader, o: int) -> dict:
    return {
        "chest":   r.u32(o + 0x00), "swch":   r.u32(o + 0x04),
        "clear":   r.u32(o + 0x08), "collect": r.u32(o + 0x0C),
        "unk":     r.u32(o + 0x10), "rooms":  r.u32(o + 0x14),
        "floors":  r.u32(o + 0x18),
    }


def parse_farores_wind(r: SlotReader, o: int) -> dict:
    # FaroresWindData: Vec3i pos (s32 x3), depois varios s32
    return {
        "pos": {"x": r.s32(o + 0x00), "y": r.s32(o + 0x04), "z": r.s32(o + 0x08)},
        "yaw":              r.s32(o + 0x0C),
        "playerParams":     r.s32(o + 0x10),
        "entranceIndex":    r.s32(o + 0x14),
        "roomIndex":        r.s32(o + 0x18),
        "set":              r.s32(o + 0x1C),
        "tempSwchFlags":    r.s32(o + 0x20),
        "tempCollectFlags": r.s32(o + 0x24),
    }


def parse_ocarina_note(r: SlotReader, o: int) -> dict:
    # OcarinaNote: 8 bytes (7 campos u8 + 1 padding)
    return {
        "noteIdx": r.u8(o + 0), "unk_01": r.u8(o + 1), "unk_02": r.u8(o + 2),
        "volume":  r.u8(o + 3), "vibrato": r.u8(o + 4), "tone": r.u8(o + 5),
        "semitone": r.u8(o + 6),
    }


def parse_base(slot_bytes: bytes) -> dict:
    """Le todos os campos da secao 'base' a partir do slot (big-endian)."""
    r = SlotReader(slot_bytes)
    if r.s[MAGIC_OFF:MAGIC_OFF + len(MAGIC)] != MAGIC:
        raise ValueError("Slot sem assinatura ZELDAZ valida.")

    base = {
        "entranceIndex":           r.s32(0x00),
        "linkAge":                 r.s32(0x04),
        "cutsceneIndex":           r.s32(0x08),
        "dayTime":                 r.u16(0x0C),
        "nightFlag":               r.s32(0x10),
        "totalDays":               r.s32(0x14),
        "bgsDayCount":             r.s32(0x18),
        "deaths":                  r.u16(0x22),
        "playerName":              r.arr(r.u8, 0x24, 8, 1),
        "healthCapacity":          r.s16(0x2E),
        "health":                  r.s16(0x30),
        "magicLevel":              r.s8(0x32),
        "magic":                   r.s8(0x33),
        "rupees":                  r.s16(0x34),
        "swordHealth":             r.u16(0x36),
        "naviTimer":               r.u16(0x38),
        "isMagicAcquired":         r.u8(0x3A),
        "isDoubleMagicAcquired":   r.u8(0x3C),
        "isDoubleDefenseAcquired": r.u8(0x3D),
        "bgsFlag":                 r.u8(0x3E),
        "ocarinaGameRoundNum":     r.u8(0x3F),
        "childEquips":             parse_item_equips(r, 0x40),
        "adultEquips":             parse_item_equips(r, 0x4A),
        "unk_54":                  r.u32(0x54),
        "savedSceneNum":           r.s16(0x66),
        "equips":                  parse_item_equips(r, 0x68),
        "inventory":               parse_inventory(r, 0x74),
        "sceneFlags":              [parse_scene_flag(r, 0xD4 + i * 0x1C) for i in range(124)],
        "fw":                      parse_farores_wind(r, 0xE64),
        "gsFlags":                 r.arr(r.s32, 0xE9C, 6, 4),
        "highScores":              r.arr(r.s32, 0xEB8, 7, 4),
        "eventChkInf":             r.arr(r.u16, 0xED4, 14, 2),
        "itemGetInf":              r.arr(r.u16, 0xEF0, 4, 2),
        "infTable":                r.arr(r.u16, 0xEF8, 30, 2),
        "worldMapAreaData":        r.u32(0xF38),
        "scarecrowLongSongSet":    r.u8(0xF40),
        "scarecrowLongSong":       [parse_ocarina_note(r, 0xF41 + i * 8) for i in range(108)],
        "scarecrowSpawnSongSet":   r.u8(0x12C5),
        "scarecrowSpawnSong":      [parse_ocarina_note(r, 0x12C6 + i * 8) for i in range(16)],
        "horseData": {
            "scene": r.s16(0x1348),
            "pos":   {"x": r.s16(0x134A), "y": r.s16(0x134C), "z": r.s16(0x134E)},
            "angle": r.s16(0x1350),
        },
    }
    return base


# Campos exclusivos do SoH que NAO existem na SRAM do N64.
# Quando ha molde, sao preservados do molde; sem molde, recebem defaults.
SOH_ONLY_FIELDS = {
    "randomizerInf", "isMasterQuest", "backupFW",
    "dogParams", "filenameLanguage", "maskMemory",
}


def _pad(lst, n, fill=255):
    return list(lst) + [fill] * (n - len(lst))


def adapt_for_soh(base: dict, heal: bool):
    """Ajustes do save N64 para o que o SoH moderno espera."""
    # Os equips do SoH foram expandidos (suporte a D-pad): 4->8 botoes, 3->7 slots.
    # Completa os slots extras com 255 (= vazio).
    for eq in ("childEquips", "adultEquips", "equips"):
        base[eq]["buttonItems"] = _pad(base[eq]["buttonItems"], 8, 255)
        base[eq]["cButtonSlots"] = _pad(base[eq]["cButtonSlots"], 7, 255)

    # Saves da VC as vezes salvam health=0 -> evita carregar morto.
    if heal and base.get("health", 0) <= 0:
        base["health"] = base["healthCapacity"]


def _default_soh_fields() -> dict:
    """Defaults para campos exclusivos do SoH (usado quando nao ha molde)."""
    fw_zero = {
        "pos": {"x": 0, "y": 0, "z": 0}, "yaw": 0, "playerParams": 0,
        "entranceIndex": 0, "roomIndex": 0, "set": 0,
        "tempSwchFlags": 0, "tempCollectFlags": 0,
    }
    return {
        "randomizerInf": [0] * 129,
        "isMasterQuest": False,
        "backupFW": fw_zero,
        "dogParams": 0,
        "filenameLanguage": 0,
        "maskMemory": 0,
    }


def build_savefile(base: dict, template: dict | None, filenum: int) -> dict:
    """Monta o JSON final do fileN.sav."""
    if template is not None:
        out = json.loads(json.dumps(template))  # copia profunda
        tgt = out["sections"]["base"]["data"]
        for k, v in base.items():
            if k in SOH_ONLY_FIELDS:
                continue  # preserva do molde
            tgt[k] = v
        return out

    # ----- Sem molde: estrutura minima do zero -----
    base = dict(base)
    base.update(_default_soh_fields())
    return {
        "fileType": 0,  # 0 = save vanilla
        "sections": {
            "base": {"version": 4, "data": base},
        },
        "version": 1,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Converte save N64 de Ocarina of Time (.sra/.srm) para o "
                    "formato JSON do Ship of Harkinian (fileN.sav)."
    )
    ap.add_argument("input", help="SRAM bruta do N64 (.sra/.srm)")
    ap.add_argument("--template", help="fileN.sav da sua versao do SoH (molde, recomendado)")
    ap.add_argument("--out", default="file1.sav", help="arquivo de saida (padrao: file1.sav)")
    ap.add_argument("--slot", type=int, choices=[1, 2, 3], default=None,
                    help="slot de origem (1-3). Padrao: detecta automaticamente.")
    ap.add_argument("--filenum", type=int, default=0,
                    help="numero do arquivo no SoH (0=File1, 1=File2, 2=File3)")
    ap.add_argument("--no-heal", action="store_true",
                    help="nao encher a vida quando o save vier com health=0")
    args = ap.parse_args(argv)

    # 1) Le e normaliza ordem de bytes
    raw = open(args.input, "rb").read()
    try:
        data, order = normalize_to_big_endian(raw)
    except ValueError as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 2
    print(f"Ordem de bytes detectada: {order}")

    # 2) Escolhe o slot
    slots = find_slots_with_save(data)
    if not slots:
        print("ERRO: nenhum slot com save valido.", file=sys.stderr)
        return 2
    print(f"Slots com save: {slots}")
    slot = args.slot if args.slot is not None else slots[0]
    if slot not in slots:
        print(f"AVISO: slot {slot} esta vazio; usando slot {slots[0]}.")
        slot = slots[0]
    print(f"Convertendo o slot {slot}.")

    off = PRIMARY_SLOTS[slot]
    slot_bytes = data[off:off + SLOT_SIZE]

    # 3) Parse + adaptacoes
    base = parse_base(slot_bytes)
    adapt_for_soh(base, heal=not args.no_heal)

    # 4) Molde (opcional)
    template = None
    if args.template:
        template = json.load(open(args.template, encoding="utf-8"))
    else:
        print("AVISO: sem --template. Gerando estrutura do zero (menos testada "
              "entre versoes; prefira fornecer um molde da sua versao do SoH).")

    out_json = build_savefile(base, template, args.filenum)

    # 5) Escreve no formato do SoH (indentacao de 1 espaco)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_json, f, indent=1, ensure_ascii=False)
        f.write("\n")

    # 6) Resumo
    hearts = base["healthCapacity"] / 16
    age = "Crianca" if base["linkAge"] == 1 else "Adulto"
    print("-" * 48)
    print(f"OK! Gerado: {args.out}")
    print(f"  Idade: {age} | Coracoes: {hearts:g} | Magia: {base['magic']} "
          f"(nivel {base['magicLevel']})")
    print(f"  Rupees: {base['rupees']} | Mortes: {base['deaths']} | "
          f"Cena salva: {base['savedSceneNum']}")
    print("Coloque o arquivo na pasta 'Save' do Ship of Harkinian.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
