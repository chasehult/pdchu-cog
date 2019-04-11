import math
import csv
import os
import io
from shutil import rmtree
import re

import discord
from discord.ext import commands
import aiohttp
from ply import lex
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageChops

from __main__ import user_allowed, send_cmd_help

from .rpadutils import ReportableError
from .rpadutils import CogSettings
from .utils import checks
from .utils.chat_formatting import box, inline

HELP_MSG = """
^buildimg <build_shorthand>

Generates an image representing a team based on a string.

Format: 
    card name(assist)[latent,latent]*repeat|Stats
    Card name must be first, otherwise the order does not matter
    Separate each card with /
    Separate each team with ;
    To use / in card name, put quote around the entire team slot (e.g. "g/l medjed(g/x zela)"/...)
    sdr is a special card name for dummy assists/skill delay buffers
Latent Acronyms:
    Separate each latent with a ,
    Killers: bak(balanced), phk(physical), hek(healer), drk(dragon), gok(god), aak(attacker, dek(devil), mak(machine)
             evk(evo mat), rek(redeemable), awk(awoken mat), enk(enhance)
    Stats (+ for 2 slot): hp, atk, rcv, all(all stat), hp+, atk+, rcv+
    Resists (+ for 2 slot): rres, bres, gres, lres, dres, rres+, bres+, gres+, lres+, dres+
    Others: sdr, ah(autoheal)
Repeat:
    *# defines number of times to repeat this particular card
    e.g. whaledor(plutus)*3/whaledor(carat)*2 creates a team of 3 whaledor(plutus) followed by 2 whaledor(carat)
    Latents can also be repeated, e.g. whaledor[sdr*5] for 5 sdr latents
Stats Format:
    | LV### SLV## AW# SA# +H## +A## +R## +(0 or 297)
    | indicates end of card name and start of stats
    LV: level, 1 to 110
    SLV: skill level, 1 to 99 or MAX
    AW: awakenings, 0 to 9
    SA: super awakening, 0 to 9
    +H: HP plus, 0 to 99
    +A: ATK plus, 0 to 99
    +R: RCV plus, 0 to 99
    +: total plus (+0 or +297 only)
    Case insensitive, order does not matter
"""
EXAMPLE_MSG = "Examples:\n1P{}\n2P{}\n3P{}\nLatent Validation{}\nStats Validation{}".format(
    box("bj(weld)lv110/baldin[gok *3](gilgamesh)/youyu(assist reeche)/mel(chocolate)/isis(koenma)/bj(rathian)"),
    box("amen/dios(sdr) * 3/whaledor; mnoah(assist jack frost) *3/tengu/tengu[sdr,sdr,sdr,sdr,sdr,sdr](durandalf)"),
    box("zela(assist amen) *3/base raizer * 2/zela; zela(assist amen) *4/base valeria/zela; zela * 6"),
    box("eir[drk,drk,sdr]/eir[bak,bak,sdr]/eir[sdr *4, dek]/eir[sdr *8, dek]"),
    box("dmeta(uruka|lv110+297slvmax)|+h33+a66+r99lv110slv15/    hmyne(buruka|lv110+297slv1)|+h99+a99+r99lv110slv15")
)

"""
Examples:
1P:
    bj(weld)lv110/baldin[gok, gok, gok](gilgamesh)/youyu(assist reeche)/mel(chocolate)/isis(koenma)/bj(rathian)
2P:
    amen/dios(sdr) * 3/whaledor; mnoah(assist jack frost) *3/tengu/tengu[sdr,sdr,sdr,sdr,sdr,sdr](durandalf)
3P:
    zela(assist amen) *3/base raizer * 2/zela; zela(assist amen) *4/base valeria/zela; zela * 6
Latent Validation:
    eir[drk,drk,sdr]/eir[bak,bak,sdr]
Stats Validation:
    dmeta(uruka|lv110+297slvmax)|+h33+a66+r99lv110slv15/    hmyne(buruka|lv110+297slv1)|+h99+a99+r99lv110slv15

"""

LATENTS_MAP = {
    1: 'bak',
    2: 'phk',
    3: 'hek',
    4: 'drk',
    5: 'gok',
    6: 'aak',
    7: 'dek',
    8: 'mak',
    9: 'evk',
    10: 'rek',
    11: 'awk',
    12: 'enk',
    13: 'all',
    14: 'hp+',
    15: 'atk+',
    16: 'rcv+',
    17: 'rres+',
    18: 'bres+',
    19: 'gres+',
    20: 'lres+',
    21: 'dres+',
    22: 'hp',
    23: 'atk',
    24: 'rcv',
    25: 'rres',
    26: 'bres',
    27: 'gres',
    28: 'lres',
    29: 'dres',
    30: 'ah',
    31: 'sdr'
}
REVERSE_LATENTS_MAP = {v: k for k, v in LATENTS_MAP.items()}
TYPE_TO_KILLERS_MAP = {
    'God': [7],  # devil
    'Devil': [5],  # god
    'Machine': [5, 1],  # god balanced
    'Dragon': [8, 3],  # machine healer
    'Physical': [8, 3],  # machine healer
    'Attacker': [7, 2],  # devil physical
    'Healer': [4, 6],  # dragon attacker
}
TS_SEQ_AWAKE_MAP = {2765: 3, 2766: 4, 2767: 5, 2768: 6, 2769: 7, 2770: 8, 2771: 9, 2772: 10, 2773: 11, 2774: 12,
                    2775: 13,
                    2776: 14, 2777: 15, 2778: 16, 2779: 17, 2780: 18, 2781: 19, 2782: 20, 2783: 21, 2784: 22, 2785: 23,
                    2786: 24, 2787: 25, 2788: 26, 2789: 27, 2790: 28, 2791: 29, 3897: 30, 7593: 31, 7878: 33, 7879: 35,
                    7880: 36, 7881: 34, 7882: 32, 9024: 37, 9025: 38, 9026: 39, 9113: 40, 9224: 41, 9397: 43, 9481: 42,
                    10261: 44, 11353: 45, 11619: 46, 12490: 47, 12735: 48, 12736: 49, 13057: 50, 13567: 51, 13764: 52,
                    13765: 53, 13898: 54, 13899: 55, 13900: 56, 13901: 57, 13902: 58, 14073: 59, 14074: 60, 14075: 61,
                    14076: 62, 14950: 63, 15821: 64, 15822: 65, 15823: 66}

AWK_CIRCLE = 'circle'
AWK_STAR = 'star'
DELAY_BUFFER = 'delay_buffer'
REMOTE_ASSET_URL = 'https://github.com/Mushymato/pdchu-cog/raw/master/assets/'


class DictWithAttributeAccess(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class PadBuildImgSettings(CogSettings):
    def make_default_build_img_params(self):
        build_img_params = DictWithAttributeAccess({
            'ASSETS_DIR': './data/padbuildimg/assets/',
            'PORTRAIT_DIR': './data/padbuildimg/portrait/',
            'OUTPUT_DIR': './data/padbuildimg/output/',
            'PORTRAIT_WIDTH': 100,
            'PADDING': 10,
            'LATENTS_WIDTH': 25,
            'FONT_NAME': './data/padbuildimg/assets/OpenSans-ExtraBold.ttf'
        })
        if not os.path.exists(build_img_params.ASSETS_DIR):
            os.mkdir(build_img_params.ASSETS_DIR)
        if not os.path.exists(build_img_params.OUTPUT_DIR):
            os.mkdir(build_img_params.OUTPUT_DIR)
        return build_img_params

    def buildImgParams(self):
        if 'build_img_params' not in self.bot_settings:
            self.bot_settings['build_img_params'] = self.make_default_build_img_params()
            self.save_settings()
        return DictWithAttributeAccess(self.bot_settings['build_img_params'])

    def setBuildImgParamsByKey(self, key, value):
        if 'build_img_params' not in self.bot_settings:
            self.bot_settings['build_img_params'] = self.make_default_build_img_params()
        if key in self.bot_settings['build_img_params']:
            self.bot_settings['build_img_params'][key] = value
        self.save_settings()

    async def downloadAssets(self, source, target):
        async with aiohttp.ClientSession() as session:
            async with session.get(source) as resp:
                data = await resp.read()
                with open(target, "wb") as f:
                    f.write(data)

    async def downloadAllAssets(self):
        params = self.buildImgParams()
        if os.path.exists(params.ASSETS_DIR):
            rmtree(params.ASSETS_DIR)
        os.mkdir(params.ASSETS_DIR)
        os.mkdir(params.ASSETS_DIR + 'lat/')
        os.mkdir(params.ASSETS_DIR + 'awk/')
        for idx, lat in LATENTS_MAP.items():
            await self.downloadAssets(
                REMOTE_ASSET_URL + 'lat/' + lat + '.png', params.ASSETS_DIR + 'lat/' + lat + '.png')
        for awk in range(3, 67):
            awk = str(awk)
            await self.downloadAssets(
                REMOTE_ASSET_URL + 'awk/' + awk + '.png', params.ASSETS_DIR + 'awk/' + awk + '.png')
        await self.downloadAssets(REMOTE_ASSET_URL + AWK_CIRCLE + '.png', params.ASSETS_DIR + AWK_CIRCLE + '.png')
        await self.downloadAssets(REMOTE_ASSET_URL + AWK_STAR + '.png', params.ASSETS_DIR + AWK_STAR + '.png')
        await self.downloadAssets(REMOTE_ASSET_URL + DELAY_BUFFER + '.png', params.ASSETS_DIR + DELAY_BUFFER + '.png')
        font_name = os.path.basename(params.FONT_NAME)
        await self.downloadAssets(REMOTE_ASSET_URL + font_name, params.ASSETS_DIR + font_name)

    def dmOnly(self, server_id):
        if 'dm_only' not in self.bot_settings:
            self.bot_settings['dm_only'] = []
            self.save_settings()
        return server_id in self.bot_settings['dm_only']

    def toggleDmOnly(self, server_id):
        if 'dm_only' not in self.bot_settings:
            self.bot_settings['dm_only'] = []
        else:
            if server_id in self.bot_settings['dm_only']:
                self.bot_settings['dm_only'].remove(server_id)
            else:
                self.bot_settings['dm_only'].append(server_id)
        self.save_settings()


class PaDTeamLexer(object):
    tokens = [
        'ID',
        'ASSIST',
        'LATENT',
        'STATS',
        'SPACES',
        'LV',
        'SLV',
        'AWAKE',
        'SUPER',
        'P_HP',
        'P_ATK',
        'P_RCV',
        'P_ALL',
        'REPEAT',
    ]

    def t_ID(self, t):
        r'^.+?(?=[\(\|\[\*])|^(?!.*[\(\|\[\*].*).+'
        # first word before ( or [ or | or * entire word if those characters are not in string
        t.value = t.value.strip()
        return t

    def t_ASSIST(self, t):
        r'\(.*?\)'
        # words in ()
        t.value = t.value.strip('()')
        return t

    def t_LATENT(self, t):
        r'\[.+?\]'
        # words in []
        t.value = [l.strip().lower() for l in t.value.strip('[]').split(',')]
        for v in t.value.copy():
            if '*' not in v:
                continue
            tmp = [l.strip() for l in v.split('*')]
            if len(tmp[0]) == 1 and tmp[0].isdigit():
                count = int(tmp[0])
                latent = tmp[1]
            elif len(tmp[1]) == 1 and tmp[1].isdigit():
                count = int(tmp[1])
                latent = tmp[0]
            else:
                continue
            idx = t.value.index(v)
            t.value.remove(v)
            for i in range(count):
                t.value.insert(idx, latent)
        t.value = t.value[0:6]
        t.value = [REVERSE_LATENTS_MAP[l] for l in t.value if l in REVERSE_LATENTS_MAP]
        return t

    def t_STATS(self, t):
        r'\|'
        pass

    def t_SPACES(self, t):
        r'\s'
        # spaces must be checked after ID
        pass

    def t_LV(self, t):
        r'[lL][vV]\s?\d{1,3}'
        # LV followed by 1~3 digit number
        t.value = int(t.value[2:])
        return t

    def t_SLV(self, t):
        r'[sS][lL][vV]\s?(\d{1,2}|[mM][aA][xX])'
        # SL followed by 1~2 digit number or max
        t.value = t.value[3:]
        if t.value.isdigit():
            t.value = int(t.value)
        else:
            t.value = 99
        return t

    def t_AWAKE(self, t):
        r'[aA][wW]\s?\d'
        # AW followed by 1 digit number
        t.value = int(t.value[2:])
        return t

    def t_SUPER(self, t):
        r'[sS][aA]\s?\d'
        # SA followed by 1 digit number
        t.value = int(t.value[2:])
        return t

    def t_P_ALL(self, t):
        r'\+\s?\d{1,3}'
        # + followed by 0 or 297
        t.value = min(int(t.value[1:]), 297)
        return t

    def t_P_HP(self, t):
        r'\+[hH]\s?\d{1,2}'
        # +H followed by 1~2 digit number
        t.value = int(t.value[2:])
        return t

    def t_P_ATK(self, t):
        r'\+[aA]\s?\d{1,2}'
        # +A followed by 1~2 digit number
        t.value = int(t.value[2:])
        return t

    def t_P_RCV(self, t):
        r'\+[rR]\s?\d{1,2}'
        # +R followed by 1~2 digit number
        t.value = int(t.value[2:])
        return t

    def t_REPEAT(self, t):
        r'\*\s?\d'
        # * followed by a number
        t.value = min(int(t.value[1:]), 6)
        return t

    t_ignore = '\t\n'

    def t_error(self, t):
        raise ReportableError("Parse Error: Unknown text '{}' at position {}".format(t.value, t.lexpos))

    def build(self, **kwargs):
        # pass debug=1 to enable verbose output
        self.lexer = lex.lex(module=self)
        return self.lexer


def validate_latents(latents, card_types):
    if latents is None:
        return None
    if card_types is None:
        return None
    if 'Balance' in card_types:
        return latents
    for idx, l in enumerate(latents):
        if 0 < l < 9:
            if not any([l in TYPE_TO_KILLERS_MAP[t] for t in card_types if t is not None]):
                latents[idx] = None
    latents = [l for l in latents if l is not None]
    return latents if len(latents) > 0 else None


def outline_text(draw, x, y, font, text_color, text, thickness=1):
    shadow_color = 'black'
    draw.text((x - thickness, y - thickness), text, font=font, fill=shadow_color)
    draw.text((x + thickness, y - thickness), text, font=font, fill=shadow_color)
    draw.text((x - thickness, y + thickness), text, font=font, fill=shadow_color)
    draw.text((x + thickness, y + thickness), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=text_color)


def trim(im):
    bg = Image.new(im.mode, im.size, (255, 255, 255, 0))
    diff = ImageChops.difference(im, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    if bbox:
        return im.crop(bbox)


def text_center_pad(font_size, line_height):
    return math.floor((line_height - font_size) / 3)


def idx_to_xy(idx):
    return idx // 2, - (idx % 2)


class PadBuildImageGenerator(object):
    def __init__(self, params, padinfo_cog, build_name='pad_build'):
        self.params = params
        self.padinfo_cog = padinfo_cog
        self.lexer = PaDTeamLexer().build()
        self.build = {
            'NAME': build_name,
            'TEAM': [],
            'INSTRUCTION': None
        }
        self.build_img = None

    def process_build(self, input_str):
        team_strings = [row for row in csv.reader(re.split('[;\n]', input_str), delimiter='/') if len(row) > 0]
        if len(team_strings) > 3:
            team_strings = team_strings[0:3]
        for team in team_strings:
            team_sublist = []
            for slot in team:
                try:
                    team_sublist.extend(self.process_card(slot))
                except Exception as ex:
                    self.build['TEAM'] = []
                    raise ex
            self.build['TEAM'].append(team_sublist)

    def process_card(self, card_str, is_assist=False):
        if not is_assist:
            result_card = {
                '+ATK': 99,
                '+HP': 99,
                '+RCV': 99,
                'AWAKE': 9,
                'SUPER': 0,
                'MAX_AWAKE': 9,
                'GOLD_STAR': True,
                'ID': 0,
                'LATENT': None,
                'LV': 99,
                'SLV': 0,
                'ON_COLOR': True
            }
        else:
            result_card = {
                '+ATK': 0,
                '+HP': 0,
                '+RCV': 0,
                'AWAKE': 0,
                'SUPER': 0,
                'MAX_AWAKE': 0,
                'GOLD_STAR': True,
                'ID': 0,
                'LATENT': None,
                'LV': 1,
                'SLV': 0,
                'MAX_SLV': 0,
                'ON_COLOR': False
            }
        if len(card_str) == 0:
            if is_assist:
                result_card['ID'] = DELAY_BUFFER
                return result_card, None
            else:
                return []
        self.lexer.input(card_str)
        assist_str = None
        card = None
        repeat = 1
        for tok in iter(self.lexer.token, None):
            # print('{} - {}'.format(tok.type, tok.value))
            if tok.type == 'ASSIST':
                assist_str = tok.value
            elif tok.type == 'REPEAT':
                repeat = min(tok.value, 6)
            elif tok.type == 'ID':
                if tok.value.lower() == 'sdr':
                    result_card['ID'] = DELAY_BUFFER
                    card = DELAY_BUFFER
                else:
                    card, err, debug_info = self.padinfo_cog.findMonster(tok.value)
                    if card is None:
                        raise ReportableError('Lookup Error: {}'.format(err))
                    if not card.is_inheritable:
                        if is_assist:
                            return None, None
                        else:
                            result_card['GOLD_STAR'] = False
                    result_card['ID'] = card.monster_no_jp
            elif tok.type == 'P_ALL':
                if tok.value >= 297:
                    result_card['+HP'] = 99
                    result_card['+ATK'] = 99
                    result_card['+RCV'] = 99
                else:
                    result_card['+HP'] = 0
                    result_card['+ATK'] = 0
                    result_card['+RCV'] = 0
            elif tok.type != 'STATS':
                result_card[tok.type.replace('P_', '+')] = tok.value
        card_att = None
        if card is None:
            return []
        elif card != DELAY_BUFFER:
            result_card['LATENT'] = validate_latents(
                result_card['LATENT'],
                [card.type1, card.type2, card.type3]
            )
            result_card['LV'] = min(
                result_card['LV'],
                110 if card.limitbreak_stats is not None and card.limitbreak_stats > 1 else card.max_level
            )
            result_card['MAX_SLV'] = card.active_skill.turn_max - card.active_skill.turn_min + 1
            result_card['MAX_AWAKE'] = len(card.awakenings) - card.superawakening_count
            if is_assist:
                result_card['MAX_AWAKE'] = result_card['MAX_AWAKE'] if result_card['AWAKE'] > 0 else 0
                result_card['AWAKE'] = result_card['MAX_AWAKE']
                result_card['SUPER'] = 0
            else:
                result_card['SUPER'] = min(result_card['SUPER'], card.superawakening_count)
                if result_card['SUPER'] > 0:
                    super_awakes = [TS_SEQ_AWAKE_MAP[x.ts_seq] for x in card.awakenings[-card.superawakening_count:]]
                    result_card['SUPER'] = super_awakes[result_card['SUPER'] - 1]
                    result_card['LV'] = max(100, result_card['LV'])
            card_att = card.attr1
        if is_assist:
            return result_card, card_att
        else:
            parsed_cards = [result_card]
            if isinstance(assist_str, str):
                assist_card, assist_att = self.process_card(assist_str, is_assist=True)
                if card_att is not None and assist_att is not None:
                    assist_card['ON_COLOR'] = card_att == assist_att
                parsed_cards.append(assist_card)
            else:
                parsed_cards.append(None)
            parsed_cards = parsed_cards * repeat
            return parsed_cards

    def combine_latents(self, latents):
        if not latents:
            return False
        if len(latents) > 6:
            latents = latents[0:6]
        latents_bar = Image.new('RGBA',
                                (self.params.PORTRAIT_WIDTH, self.params.LATENTS_WIDTH * 2),
                                (255, 255, 255, 0))
        x_offset = 0
        y_offset = 0
        row_count = 0
        one_slot, two_slot = [], []
        for l in latents:
            if l < 22:
                two_slot.append(l)
            else:
                one_slot.append(l)
        sorted_latents = []
        if len(one_slot) > len(two_slot):
            sorted_latents.extend(one_slot)
            sorted_latents.extend(two_slot)
        else:
            sorted_latents.extend(two_slot)
            sorted_latents.extend(one_slot)
        last_height = 0
        for l in sorted_latents:
            latent_icon = Image.open(self.params.ASSETS_DIR + 'lat/' + LATENTS_MAP[l] + '.png')
            if x_offset + latent_icon.size[0] > self.params.PORTRAIT_WIDTH:
                row_count += 1
                x_offset = 0
                y_offset += last_height
            latents_bar.paste(latent_icon, (x_offset, y_offset))
            last_height = latent_icon.size[1]
            x_offset += latent_icon.size[0]
            if row_count == 1 and x_offset >= self.params.LATENTS_WIDTH * 2:
                break
        return latents_bar

    def combine_portrait(self, card, show_stats=True, show_supers=False):
        if card['ID'] == DELAY_BUFFER:
            return Image.open(self.params.ASSETS_DIR + DELAY_BUFFER + '.png')
        portrait = Image.open(self.params.PORTRAIT_DIR + str(card['ID']) + '.png')
        draw = ImageDraw.Draw(portrait)
        slv_offset = 80
        if show_stats:
            # + eggs
            sum_plus = card['+HP'] + card['+ATK'] + card['+RCV']
            if 0 < sum_plus:
                if sum_plus < 297:
                    font = ImageFont.truetype(self.params.FONT_NAME, 14)
                    outline_text(draw, 5, 2, font, 'yellow', '+{:d} HP'.format(card['+HP']))
                    outline_text(draw, 5, 14, font, 'yellow', '+{:d} ATK'.format(card['+ATK']))
                    outline_text(draw, 5, 26, font, 'yellow', '+{:d} RCV'.format(card['+RCV']))
                else:
                    font = ImageFont.truetype(self.params.FONT_NAME, 18)
                    outline_text(draw, 5, 0, font, 'yellow', '+297')
            # level
            if card['LV'] > 0:
                outline_text(draw, 5, 75, ImageFont.truetype(self.params.FONT_NAME, 18),
                             'white', 'Lv.{:d}'.format(card['LV']))
                slv_offset = 65
        # skill level
        if card['SLV'] > 0:
            slv_txt = 'SLv.max' if card['SLV'] >= card['MAX_SLV'] else 'SLv.{:d}'.format(card['SLV'])
            outline_text(draw, 5, slv_offset,
                         ImageFont.truetype(self.params.FONT_NAME, 12), 'pink', slv_txt)
        # ID
        outline_text(draw, 67, 82, ImageFont.truetype(self.params.FONT_NAME, 12), 'lightblue', str(card['ID']))
        del draw
        if card['MAX_AWAKE'] > 0:
            # awakening
            if card['AWAKE'] >= card['MAX_AWAKE']:
                awake = Image.open(self.params.ASSETS_DIR + AWK_STAR + '.png')
            else:
                awake = Image.open(self.params.ASSETS_DIR + AWK_CIRCLE + '.png')
                draw = ImageDraw.Draw(awake)
                draw.text((8, -2), str(card['AWAKE']),
                          font=ImageFont.truetype(self.params.FONT_NAME, 18), fill='yellow')
                del draw
            portrait.paste(awake, (self.params.PORTRAIT_WIDTH - awake.size[0] - 5, 5), awake)
            awake.close()
        if show_supers and card['SUPER'] > 0:
            # SA
            awake = Image.open(self.params.ASSETS_DIR + 'awk/' + str(card['SUPER']) + '.png')
            portrait.paste(awake,
                           (self.params.PORTRAIT_WIDTH - awake.size[0] - 5,
                            (self.params.PORTRAIT_WIDTH - awake.size[0]) // 2),
                           awake)
            awake.close()
        return portrait

    def generate_build_image(self, include_instructions=False):
        if self.build is None:
            return
        team_size = max([len(x) for x in self.build['TEAM']])
        p_w = self.params.PORTRAIT_WIDTH * math.ceil(team_size / 2) + \
              self.params.PADDING * math.ceil(team_size / 10)
        p_h = (self.params.PORTRAIT_WIDTH + self.params.LATENTS_WIDTH + self.params.PADDING) * \
              2 * len(self.build['TEAM'])
        include_instructions &= self.build['INSTRUCTION'] is not None
        if include_instructions:
            p_h += len(self.build['INSTRUCTION']) * (self.params.PORTRAIT_WIDTH // 2 + self.params.PADDING)
        self.build_img = Image.new('RGBA',
                                   (p_w, p_h),
                                   (255, 255, 255, 0))
        y_offset = 0
        for team in self.build['TEAM']:
            has_assist = any([card is not None for idx, card in enumerate(team) if idx % 2 == 1])
            has_latents = any([card['LATENT'] is not None for idx, card in enumerate(team)
                               if idx % 2 == 0 and card is not None])
            if has_assist:
                y_offset += self.params.PORTRAIT_WIDTH
            for idx, card in enumerate(team):
                if idx > 11 or idx > 9 and len(self.build['TEAM']) % 2 == 0:
                    break
                if card is not None:
                    x, y = idx_to_xy(idx)
                    portrait = self.combine_portrait(
                        card,
                        show_stats=card['ON_COLOR'],
                        show_supers=len(self.build['TEAM']) == 1)
                    if portrait is None:
                        continue
                    x_offset = self.params.PADDING * math.ceil(x / 4)
                    self.build_img.paste(
                        portrait,
                        (x_offset + x * self.params.PORTRAIT_WIDTH,
                         y_offset + y * self.params.PORTRAIT_WIDTH))
                    if has_latents and idx % 2 == 0 and card['LATENT'] is not None:
                        latents = self.combine_latents(card['LATENT'])
                        self.build_img.paste(
                            latents,
                            (x_offset + x * self.params.PORTRAIT_WIDTH,
                             y_offset + (y + 1) * self.params.PORTRAIT_WIDTH))
                        latents.close()
                    portrait.close()
            y_offset += self.params.PORTRAIT_WIDTH + self.params.PADDING * 2
            if has_latents:
                y_offset += self.params.LATENTS_WIDTH * 2

        if include_instructions:
            y_offset -= self.params.PADDING * 2
            draw = ImageDraw.Draw(self.build_img)
            font = ImageFont.truetype(self.params.FONT_NAME, 24)
            text_padding = text_center_pad(25, self.params.PORTRAIT_WIDTH // 2)
            for step in self.build['INSTRUCTION']:
                x_offset = self.params.PADDING
                outline_text(draw, x_offset, y_offset + text_padding,
                             font, 'white', 'F{:d} - P{:d} '.format(step['FLOOR'], step['PLAYER'] + 1))
                x_offset += self.params.PORTRAIT_WIDTH
                if step['ACTIVE'] is not None:
                    actives_used = [str(self.build['TEAM'][idx][ids]['ID'])
                                    for idx, side in enumerate(step['ACTIVE'])
                                    for ids in side]
                    for card in actives_used:
                        p_small = Image.open(self.params.PORTRAIT_DIR + str(card) + '.png') \
                            .resize((self.params.PORTRAIT_WIDTH // 2, self.params.PORTRAIT_WIDTH // 2), Image.LINEAR)
                        self.build_img.paste(p_small, (x_offset, y_offset))
                        x_offset += self.params.PORTRAIT_WIDTH // 2
                    x_offset += self.params.PADDING
                outline_text(draw, x_offset, y_offset + text_padding, font, 'white', step['ACTION'])
                y_offset += self.params.PORTRAIT_WIDTH // 2
            del draw

        self.build_img = trim(self.build_img)


class PadBuildImage:
    """PAD Build Image Generator."""

    def __init__(self, bot):
        self.bot = bot
        self.settings = PadBuildImgSettings("padbuildimg")

    @commands.command(pass_context=True)
    async def helpbuildimg(self, ctx):
        """Help info for the buildimage command."""
        await self.bot.whisper(box(HELP_MSG))
        if checks.admin_or_permissions(manage_server=True):
            await self.bot.whisper(box('For Server Admins: Output location can be changed between current channel and '
                                       'direct messages via ^togglebuildimgoutput'))
        await self.bot.whisper(EXAMPLE_MSG)

    @commands.command(pass_context=True, aliases=['buildimg', 'pdchu'])
    async def padbuildimg(self, ctx, *, build_str: str):
        """Create a build image based on input.
        Use ^helpbuildimg for more info.
        """
        # print('BUILD_STR: {}'.format(build_str))

        # start = time.perf_counter()
        params = self.settings.buildImgParams()
        try:
            pbg = PadBuildImageGenerator(params, self.bot.get_cog('PadInfo'))
            # print('PARSE: {}'.format(time.perf_counter() - start))
            pbg.process_build(build_str)
            # start = time.perf_counter()
            pbg.generate_build_image()
            # print('DRAW: {}'.format(time.perf_counter() - start))
        except ReportableError as ex:
            await self.bot.say(box(str(ex) + '\nSee ^helpbuildimg for syntax'))
            return -1

        # start = time.perf_counter()
        if pbg.build_img is not None:
            with io.BytesIO() as build_io:
                pbg.build_img.save(build_io, format='PNG')
                build_io.seek(0)
                if self.settings.dmOnly(ctx.message.server.id):
                    try:
                        await self.bot.send_file(
                            ctx.message.author,
                            fp=build_io,
                            filename='pad_build.png')
                        await self.bot.say(inline('Sent build to {}'.format(ctx.message.author)))
                    except discord.errors.Forbidden as ex:
                        await self.bot.say(inline('Failed to send build to {}'.format(ctx.message.author)))
                else:
                    await self.bot.send_file(
                        ctx.message.channel,
                        fp=build_io,
                        filename='pad_build.png')
        else:
            await self.bot.say(box('Invalid build, see ^helpbuildimg'))
        return 0

    @commands.command(pass_context=True)
    @checks.is_owner()
    async def configbuildimg(self, ctx, param_key: str, param_value: str):
        """
        Configure PadBuildImageGenerator parameters:
            ASSETS_DIR - directory for storing assets (use ^refreshassets to update)
            PORTRAIT_DIR - path to pad monster portraits with name format of <monster_no>.png
            PORTRAIT_WIDTH - width of portraits, default 100
            PADDING - padding between various things, default 10
            LATENTS_WIDTH - width of 1 slot latent, default 25
            FONT_NAME - path to font
        """
        if param_key in ['PORTRAIT_WIDTH', 'PADDING', 'LATENTS_WIDTH']:
            param_value = int(param_value)
        if param_key in ['ASSETS_DIR', 'PORTRAIT_DIR', 'OUTPUT_DIR'] \
                and param_value[-1] not in ['/', '\\']:
            param_value += '/'
        self.settings.setBuildImgParamsByKey(param_key, param_value)
        await self.bot.say(box('Set {} to {}'.format(param_key, param_value)))

    @commands.command(pass_context=True)
    @checks.is_owner()
    async def refreshassets(self, ctx):
        """
        Refresh assets folder
        """
        await self.bot.say('Downloading assets to {}'.format(self.settings.buildImgParams().ASSETS_DIR))
        await self.settings.downloadAllAssets()
        await self.bot.say('Done')

    @commands.command(pass_context=True)
    @checks.admin_or_permissions(manage_server=True)
    async def togglebuildimgoutput(self, ctx):
        """
        Toggles between sending result to server vs sending result to direct message
        """
        self.settings.toggleDmOnly(ctx.message.server.id)
        if self.settings.dmOnly(ctx.message.server.id):
            await self.bot.say('Response mode set to direct message')
        else:
            await self.bot.say('Response mode set to current channel')


def setup(bot):
    n = PadBuildImage(bot)
    bot.add_cog(n)