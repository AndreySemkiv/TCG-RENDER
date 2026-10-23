"""Bounded TMP layout using original font metrics, without raster resizing.

Reference semantics: Unity-Technologies/uGUI Runtime/TMP/TextMeshProUGUI.cs.
This is a subset: wrapping, point-size fitting, original line/character spacing,
and the ordinary alignments. It does not execute the complete TMP runtime.
"""
import copy
import math
import unicodedata

from .text_tokens import tokens, space_advance


def _cjk(char):
    return unicodedata.east_asian_width(char) in ('W','F')


def _token_font(token,font,text,fonts):
    return fonts[text['rich_styles'][token[2]]['font_id']] if len(token)>2 and token[2] else font


def token_size(token,size,text):
    if len(token)>2 and token[2]:size=text['rich_styles'][token[2]].get('font_size',size)
    return size*(token[5] if len(token)>5 else 1)


def token_spacing(token,spacing):
    return spacing*(token[5] if len(token)>5 else 1)


def _advance(token, font, size, text=None, fonts=None, pen=0):
    if token[0]=='tab':
        selected=_token_font(token,font,text,fonts)
        face=selected['face_info']
        tab=face['m_TabWidth']*selected['font_style_values']['tabSize']*token_size(token,size,text)/face['m_PointSize']*face['m_Scale']
        if tab<=0:raise ValueError('Original TMP tab width is not positive')
        # TMP_Text: ceil(xAdvance/tabSize); on a stop advance one full stop.
        stop=math.ceil(pen/tab)*tab
        return stop-pen if stop>pen else tab
    if token[0]=='sprite':
        from .inline_ex import sprite_geometry
        return sprite_geometry(token,text,_token_font(token,font,text,fonts),size)['advance']
    if token[0] != 'char':
        return space_advance(token,size)
    font=_token_font(token,font,text,fonts)
    size=token_size(token,size,text)
    lookup = font['unicode_lookup'].get(str(ord(token[1])))
    if lookup is None:
        raise ValueError('Missing original glyph U+%04X' % ord(token[1]))
    glyph = next(g for g in font['glyphs'] if g['m_Index'] == lookup['glyph_index'])
    horizontal=token[3] if len(token)>3 else (text or {}).get('horizontal_scale',1)
    return glyph['m_Metrics']['m_HorizontalAdvance'] * size/font['face_info']['m_PointSize'] * font['face_info']['m_Scale'] * lookup['scale'] * glyph['m_Scale'] * horizontal * (text or {}).get('character_width_scale',1)


def line_width(line,font,size,spacing,text=None,fonts=None):
    # TMP lineInfo.maxAdvance follows the last visible character. Spaces are
    # visible for modes2/3; TAB is visible for every mode. A whitespace-only
    # line has no visible character, so TMP retains its last character advance.
    mode=(text or {}).get('original_tmp',{}).get('m_TextWrappingMode',0)
    if text is not None and mode not in (2,3) and any(
            t[0] in ('tab','sprite') or (t[0]=='char' and not t[1].isspace()) for t in line):
        end=len(line)
        while end and (line[end-1][0]=='indent_close' or
                       (line[end-1][0]=='char' and line[end-1][1].isspace())):end-=1
        line=line[:end]
    if any(t[0] in ('tab','indent_px','indent_close','position_px') for t in line):
        pen=0
        visible=[i for i,t in enumerate(line) if t[0] in ('char','sprite','tab')]
        last=visible[-1] if visible else -1
        for i,token in enumerate(line):
            if token[0] in ('indent_px','position_px'):
                pen=token[1];continue
            if token[0]=='indent_close':continue
            pen+=_advance(token,font,size,text,fonts,pen=pen)
            if token[0] in ('char','sprite') and i!=last:pen+=token_spacing(token,spacing)
        return pen
    visible=[t for t in line if t[0] in ('char','sprite')]
    return sum(_advance(t,font,size,text,fonts) for t in line) + sum(token_spacing(t,spacing) for t in visible[:-1])


def _wrap(paragraph,font,size,spacing,width,text=None,fonts=None,initial_indent=0,preserve_whitespace=False):
    result, current = [], []
    indent=initial_indent
    # CJK follows per-character opportunities, except leading punctuation.
    forbidden_start = set('、。，．！？)]}〉》」』】〕）］｝ーぁぃぅぇぉっゃゅょァィゥェォッャュョ')
    for token in paragraph:
        current.append(token)
        if width is None or line_width(current,font,size,spacing,text,fonts) <= width+1e-5 or len(current)==1:
            continue
        breaks = []
        for i in range(1,len(current)):
            before, after = current[i-1], current[i]
            if len(before)>4 and before[4] and len(after)>4 and after[4]:continue
            if before[0] in ('char','tab') and before[1].isspace() and before[1] not in ('\u00a0','\u202f'):
                breaks.append(i)
            elif before[0]=='char' and after[0]=='char' and (_cjk(before[1]) or _cjk(after[1])) and after[1] not in forbidden_start:
                breaks.append(i)
        if not breaks and len(current[-1])>4 and current[-1][4]:continue
        split = breaks[-1] if breaks else len(current)-1
        first, current = current[:split],current[split:]
        if not preserve_whitespace:
            while first and first[-1][0]=='char' and first[-1][1].isspace(): first.pop()
            while current and current[0][0]=='char' and current[0][1].isspace(): current.pop(0)
        # The original indent remains active after a soft wrap. Closing the
        # tag changes following lines, without moving the current pen.
        for control in first:
            if control[0] in ('indent_px','indent_close'):indent=control[1]
        result.append(first)
        if indent:current.insert(0,('position_px',indent))
    result.append(current)
    return result


def measure_text(text,font,width=None,fonts=None):
    """Return source-metric lines/height, suitable for preferred-height groups.

    Set ``wrap`` explicitly on newly implemented fields; existing proof fields
    keep their legacy compiler geometry unless layout_engine is selected.
    """
    original = text.get('original_tmp',{})
    size = float(text['font_size'])
    mode=original.get('m_TextWrappingMode',0)
    if mode not in (0,1,2,3):raise ValueError('Original TMP wrapping mode is not recovered: '+str(mode))
    # TMP_Text.enableWordWrapping: Normal(1) and PreserveWhitespace(2).
    # An explicit caller override controls wrapping only; serialized modes
    # 2/3 still retain every whitespace token and its original advance.
    wrap = text.get('wrap',mode in (1,2))
    preserve_whitespace=mode in (2,3)
    if width is None and text.get('render_rect_top_left'):
        width = text['render_rect_top_left'][2]
    def layout(size):
        spacing = text.get('character_spacing_tmp_units',0)*size/100*text.get('character_width_scale',1)
        paragraphs = [[]];indent=0;block_indent=0;indent_stack=[];paragraph_indents=[0]
        for token in tokens(text):
            if token[0].startswith('line_indent_'):
                value=token[1]*size if token[0]=='line_indent_em' else token[1]
                paragraphs[-1].append(('space_px',value));indent=value
            elif token[0] in ('indent_em','indent_px'):
                indent_stack.append(block_indent)
                block_indent=token[1]*size*(token[5] if len(token)>5 else 1) if token[0]=='indent_em' else token[1]
                paragraphs[-1].append(('indent_px',block_indent))
            elif token[0]=='indent_close':
                if not indent_stack:raise ValueError('Unmatched original TMP indent close')
                block_indent=indent_stack.pop();paragraphs[-1].append(('indent_close',block_indent))
            elif token == ('char','\n'):
                paragraphs.append([('position_px',block_indent+indent)] if block_indent+indent else [])
                paragraph_indents.append(block_indent)
            else: paragraphs[-1].append(token)
        if indent_stack:raise ValueError('Unclosed original TMP indent')
        lines, final = [], []
        for paragraph,initial_indent in zip(paragraphs,paragraph_indents):
            wrapped = _wrap(paragraph,font,size,spacing,width if wrap else None,text,fonts,initial_indent,preserve_whitespace)
            lines.extend(wrapped)
            final.extend([False]*(len(wrapped)-1)+[True])
        face=font['face_info']
        scale=size/face['m_PointSize']*face['m_Scale']
        ascent,descent=face['m_AscentLine']*scale,face['m_DescentLine']*scale
        step=face['m_LineHeight']*scale+text.get('line_spacing_tmp_units',0)*size/100
        if text.get('rich_styles'):
            for key in {t[2] for line in lines for t in line if t[0]=='char' and len(t)>2 and t[2]}:
                style=text['rich_styles'][key];ff=fonts[style['font_id']]['face_info'];ss=style.get('font_size',size)/ff['m_PointSize']*ff['m_Scale']
                ascent=max(ascent,ff['m_AscentLine']*ss);descent=min(descent,ff['m_DescentLine']*ss)
                step=max(step,ff['m_LineHeight']*ss+text.get('line_spacing_tmp_units',0)*size/100)
        height=ascent-descent+max(0,len(lines)-1)*step
        return {'lines':lines,'paragraph_ends':final,'widths':[line_width(line,font,size,spacing,text,fonts) for line in lines],
                'height':height,'line_step':step,'ascent':ascent,'descent':descent,
                'font_size':size,'spacing':spacing,'wrap':wrap,
                'wrapping_mode':mode,'preserve_whitespace':preserve_whitespace}
    result=layout(size)
    if text.get('fit_font',False) and original.get('m_enableAutoSizing'):
        # Fit only inside serialized bounds; no raster resampling is performed.
        minimum=min(size,float(original.get('m_fontSizeMin',size)))
        max_height=text.get('render_rect_top_left',[0,0,0,float('inf')])[3]
        def fits(value):
            return (width is None or max(value['widths'],default=0)<=width+1e-5) and value['height']<=max_height+1e-5
        if not fits(result):
            low,high=minimum,size
            for _ in range(20):
                mid=(low+high)/2; candidate=layout(mid)
                if fits(candidate): low=mid
                else: high=mid
            result=layout(low)
    result['width']=max(result['widths'],default=0)
    return result
