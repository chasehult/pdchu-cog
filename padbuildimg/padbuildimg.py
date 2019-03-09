import json
import math
import csv

import discord
from discord.ext import commands
from ply import lex, yacc
from png import itertools
from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont
from PIL import ImageChops

from __main__ import user_allowed, send_cmd_help

from . import rpadutils
from . import padinfo
from .rpadutils import CogSettings
from .utils import checks
from .utils.chat_formatting import box, inline, pagify



HELP_MSG = """
^buildimg <build_shorthand>

Generates an image representing a team.

Format: 
    card name(assist)[latent,latent]|Stats
    Card name must be first, otherwise the order does not matter
    Separate each card with /
    Separate each team with ;
    To use / in card name, put quote around the entire team slot (e.g. "g/l medjed(g/x zela)"/...)
Latent Acronyms:
    Killers: bak(balanced), phk(physical), hek(healer), drk(dragon), gok(god), aak(attacker, dek(devil), mak(machine)
             evk(evo mat), rek(redeemable), awk(awoken mat), enk(enhance)
    Stats (+ for 2 slot): hp, atk, rcv, all(all stat), hp+, atk+, rcv+
    Resists (+ for 2 slot): rres, bres, gres, lres, dres, rres+, bres+, gres+, lres+, dres+
    Others: sdr, ah(autoheal)
Stats Format:
    | LV### SLV## AW# +H## +A## +R## +(297|0)
    | indicates end of card name and start of stats
    LV: level, 1 to 110
    SLV: skill level, 1 to 99
    AW: awakenings, 0 to 9
    +H: HP plus, 0 to 99
    +A: ATK plus, 0 to 99
    +R: RCV plus, 0 to 99
    +: total plus (+0 or +297 only)
    Case insensitive, order does not matter
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


class DictWithAttributeAccess(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class PadBuildImgSettings(CogSettings):
    def make_default_build_img_params(self):
        build_img_params = DictWithAttributeAccess({
            'ASSETS_DIR': './assets/',
            'PORTRAIT_DIR': './portrait/',
            'OUTPUT_DIR': './output/',
            'PORTRAIT_WIDTH': 100,
            'PADDING': 10,
            'LATENTS_WIDTH': 25,
            'FONT_NAME': 'OpenSans-ExtraBold.ttf'
        })
        return build_img_params

    def emojiServers(self):
        key = 'emoji_servers'
        if key not in self.bot_settings:
            self.bot_settings[key] = []
        return self.bot_settings[key]

    def setEmojiServers(self, emoji_servers):
        es = self.emojiServers()
        es.clear()
        es.extend(emoji_servers)
        self.save_settings()

    def buildImgParams(self):
        if 'build_img_params' not in self.bot_settings:
            self.bot_settings['build_img_params'] = self.make_default_build_img_params()
        return DictWithAttributeAccess(self.bot_settings['build_img_params'])

    def setBuildImgParamsByKey(self, key, value):
        if 'build_img_params' not in self.bot_settings:
            self.bot_settings['build_img_params'] = self.make_default_build_img_params()
        if key in self.bot_settings['build_img_params']:
            self.bot_settings['build_img_params'][key] = value
            self.save_settings()


class PaDTeamLexer(object):
    tokens = [
        'ID',
        'ASSIST',
        'LATENT',
        'STATS',
        'LV',
        'SLV',
        'AWAKE',
        'P_HP',
        'P_ATK',
        'P_RCV',
        'P_ALL',
    ]

    def t_ID(self, t):
        r'^.+?(?=[\(\|\[])|^(?!.*[\(\|\[].*).+'
        # first word before ( or [ or { or entire word if those characters are not in string
        return t

    def t_ASSIST(self, t):
        r'\(.+?\)'
        # words in ()
        t.value = t.value.strip('()')
        return t

    def t_LATENT(self, t):
        r'\[.+?\]'
        # words in []
        t.value = [REVERSE_LATENTS_MAP[l] for l in t.value.strip('[]').split(',')]
        return t

    def t_LV(self, t):
        r'[lL][vV]\d{1,3}'
        # LV followed by 1~3 digit number
        t.value = int(t.value[2:])
        if t.value > 110:
            t.value = 110
        return t

    def t_SLV(self, t):
        r'[sS][lL][vV]\d{1,2}'
        # SL followed by 1~2 digit number
        t.value = int(t.value[3:])
        return t

    def t_AWAKE(self, t):
        r'[aA][wW]\d'
        # AW followed by 1 digit number
        t.value = int(t.value[2:])
        return t

    def t_STATS(self, t):
        r'\|'
        return t

    def t_P_ALL(self, t):
        r'\+\d{1,3}'
        # + followed by 0 or 297
        t.value = int(t.value[1:])
        if t.value > 297:
            t.value = 297
        return t

    def t_P_HP(self, t):
        r'\+[hH]\d{1,2}'
        # +H followed by 2 digit number
        t.value = int(t.value[2:])
        return t

    def t_P_ATK(self, t):
        r'\+[aA]\d{1,2}'
        # +A followed by 2 digit number
        t.value = int(t.value[2:])
        return t

    def t_P_RCV(self, t):
        r'\+[rR]\d{1,2}'
        # +R followed by 2 digit number
        t.value = int(t.value[2:])
        return t

    t_ignore = ' \t\n'

    def t_error(self, t):
        raise ValueError("Unknown text '{}' at position {}".format(t.value, t.lexpos))

    def build(self, **kwargs):
        # pass debug=1 to enable verbose output
        self.lexer = lex.lex(module=self)
        return self.lexer


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


def filename(name):
    keep_characters = ('.', '_')
    return "".join(c for c in name if c.isalnum() or c in keep_characters).rstrip()


def text_center_pad(font_size, line_height):
    return math.floor((line_height - font_size) / 3)


def idx_to_xy(idx):
        return idx // 2, (idx + 1) % 2


class PadBuildImageGenerator(object):
    def __init__(self, input_str, params, padinfo_cog, build_name='pad_build'):
        self.params = params
        self.padinfo_cog = padinfo_cog
        self.lexer = PaDTeamLexer().build()
        self.err_msg = []
        self.build = {
            'NAME': build_name,
            'TEAM': [],
            'INSTRUCTION': None
        }
        for team in [row for row in csv.reader(input_str.split(';'), delimiter='/')]:
            team_sublist = []
            for slot in team:
                team_sublist.extend(self.process_card(slot))
            self.build['TEAM'].append(team_sublist)
        self.build_img = None

    def process_card(self, card_str, is_assist=False):
        self.lexer.input(card_str)
        if not is_assist:
            result_card = {
                '+ATK': 99,
                '+HP': 99,
                '+RCV': 99,
                'AWAKE': 9,
                'ID': 1,
                'LATENT': None,
                'LV': 99,
                'SLV': 0
            }
        else:
            result_card = {
                '+ATK': 0,
                '+HP': 0,
                '+RCV': 0,
                'AWAKE': 9,
                'ID': 1,
                'LATENT': None,
                'LV': 0,
                'SLV': 0
            }
        assist_str = None
        try:
            for tok in iter(self.lexer.token, None):
                if tok.type == 'ASSIST':
                    assist_str = tok.value
                elif tok.type == 'ID':
                    if tok.value == 'sdr':
                        result_card['ID'] = 'delay_buffer'
                    else:
                        m, err, debug_info = self.padinfo_cog.findMonster(tok.value)
                        if m is None:
                            raise ValueError('{} not found'.format(tok.value))
                        result_card['ID'] = m.monster_no
                elif tok.type == 'P_ALL':
                    if tok.value == 0:
                        result_card['+HP'] = 0
                        result_card['+ATK'] = 0
                        result_card['+RCV'] = 0
                    else:
                        result_card['+HP'] = 99
                        result_card['+ATK'] = 99
                        result_card['+RCV'] = 99
                elif tok.type != 'STATS':
                    result_card[tok.type.replace('P_', '+')] = tok.value
        except Exception as ex:
            self.err_msg.append(str(ex))
            return result_card if is_assist else [None, None]

        if is_assist:
            return result_card
        else:
            if isinstance(assist_str, str):
                return [result_card, self.process_card(assist_str, is_assist=True)]
            else:
                return [result_card, None]

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
            latent_icon = Image.open(self.params.ASSETS_DIR + LATENTS_MAP[l] + '.png')
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

    def combine_portrait(self, card, show_awakes):
        if card['ID'] == 'delay_buffer':
            return Image.open(self.params.ASSETS_DIR + 'delay_buffer.png')
        portrait = Image.open(self.params.PORTRAIT_DIR + str(card['ID']) + '.png')
        draw = ImageDraw.Draw(portrait)
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
        slv_offset = 80
        if card['LV'] > 0:
            outline_text(draw, 5, 75, ImageFont.truetype(self.params.FONT_NAME, 18),
                         'white', 'Lv.{:d}'.format(card['LV']))
            slv_offset = 62
        # skill level
        if card['SLV'] > 0:
            outline_text(draw, 5, slv_offset,
                         ImageFont.truetype(self.params.FONT_NAME, 14), 'pink', 'SLv.{:d}'.format(card['SLV']))
        # ID
        outline_text(draw, 67, 82, ImageFont.truetype(self.params.FONT_NAME, 12), 'lightblue', str(card['ID']))
        del draw
        if show_awakes:
            # awakening
            if card['AWAKE'] >= 9:
                awake = Image.open(self.params.ASSETS_DIR + 'star.png')
            else:
                awake = Image.open(self.params.ASSETS_DIR + 'circle.png')
                draw = ImageDraw.Draw(awake)
                draw.text((9, -2), str(card['AWAKE']),
                          font=ImageFont.truetype(self.params.FONT_NAME, 24), fill='yellow')
                del draw
            awake.thumbnail((25, 30), Image.LINEAR)
            portrait.paste(awake, (self.params.PORTRAIT_WIDTH - awake.size[0] - 5, 5), awake)
            awake.close()
        return portrait

    def generate_build_image(self, include_instructions=False):
        if self.build is None:
            self.err_msg.append('Empty build')
            return
        p_w = self.params.PORTRAIT_WIDTH * math.ceil(len(self.build['TEAM'][0]) / 2) + \
              self.params.PADDING * math.ceil(len(self.build['TEAM'][0]) / 10)
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
            has_assist = not any([card is None for idx, card in enumerate(team) if idx % 2 == 1])
            has_latents = any([card['LATENT'] is not None for idx, card in enumerate(team)
                               if idx % 2 == 0 and card is not None])
            for idx, card in enumerate(team):
                if idx > 11 or idx > 9 and len(self.build['TEAM']) > 1:
                    break
                if card:
                    x, y = idx_to_xy(idx)
                    portrait = self.combine_portrait(card, idx % 2 == 0)
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
            y_offset += self.params.PORTRAIT_WIDTH + self.params.PADDING * 2
            if has_assist:
                y_offset += self.params.PORTRAIT_WIDTH
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

    def save_current_build_img(self):
        fpath = self.params.OUTPUT_DIR + filename(self.build['NAME']) + '.png'
        if self.build_img is not None:
            self.build_img.save(fpath)
            return fpath

    def save_current_build_json(self):
        fpath = self.params.OUTPUT_DIR + filename(self.build['NAME']) + '.json'
        if self.build is not None:
            with open(fpath, 'w') as fp:
                json.dump(self.build, fp, indent=4, sort_keys=True)
            return fpath


class PadBuildImage:
    """PAD Build Image Generator."""

    def __init__(self, bot):
        self.bot = bot
        self.settings = PadBuildImgSettings("padbuildimg")

    @commands.command(pass_context=True)
    async def helpbuildimg(self, ctx):
        """Help info for the search command."""
        await self.bot.whisper(box(HELP_MSG))

    @commands.command(pass_context=True, aliases=['buildimg', 'pdchu'])
    async def padbuildimg(self, ctx, *, build_str: str):
        """Create a build image based on input.
        Use ^helpbuildimg for more info.
        """
        params = self.settings.buildImgParams()

        pbg = PadBuildImageGenerator(build_str, params, self.bot.get_cog('PadInfo'))
        pbg.generate_build_image()
        pbg.save_current_build_json()
        fpath = pbg.save_current_build_img()
        if fpath:
            with open(fpath, mode='rb') as data:
                await self.bot.send_file(ctx.message.channel, fp=data, filename='pad_build.png')
        else:
            await self.bot.say(box('Invalid build, see ^helpbuildimg'))

    @commands.command(pass_context=True)
    @checks.is_owner()
    async def configbuildimg(self, ctx, param_key: str, param_value: str):
        """
        Configure PadBuildImageGenerator parameters:
            ASSETS_DIR
            PORTRAIT_DIR
            OUTPUT_DIR
            PORTRAIT_WIDTH
            PADDING
            LATENTS_WIDTH
            FONT_NAME
        """
        if param_key in ['PORTRAIT_WIDTH', 'PADDING', 'LATENTS_WIDTH']:
            param_value = int(param_value)
        if param_key in ['ASSETS_DIR', 'PORTRAIT_DIR', 'OUTPUT_DIR'] \
                and param_value[-1] not in ['/', '\\']:
            print(param_value[-1])
            param_value += '/'
        self.settings.setBuildImgParamsByKey(param_key, param_value)
        await self.bot.say(box('Set {} to {}'.format(param_key, param_value)))


def setup(bot):
    n = PadBuildImage(bot)
    bot.add_cog(n)
