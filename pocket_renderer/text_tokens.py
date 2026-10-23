"""Supported original TMP text commands, retaining source text unchanged.

Horizontal space semantics: Unity TextMeshPro RichTextSpace documentation.
https://docs.unity3d.com/Packages/com.unity.textmeshpro@4.0/manual/RichTextSpace.html
This is deliberately a bounded parser, not a complete TMP rich-text engine.
"""
import re

SPACE = re.compile(r'<space=([+-]?(?:\d+(?:\.\d*)?|\.\d+))(em|px)?>', re.IGNORECASE)
FONT_COMMAND = re.compile(r'\[(/?)Ctrl:(Bold|Italic)(?:\s+[^\[\]]*)?\]')
NO_BREAK = re.compile(r'\[(/?)Ctrl:NoBreak(?:\s+[^\[\]]*)?\]')
LAYOUT = re.compile(r'<(line-indent|indent|scale)=([+-]?(?:\d+(?:\.\d*)?|\.\d+))(em|px)?>|<(br|/line-indent|/indent|/scale|nobr|/nobr)>',re.I)
THREE_SPACE = re.compile(r'\[Text:Char\s+v\s*=\s*"THREE-PER-EM-SPACE"\s*\]')
BRACKET = re.compile(r'\[C:(Lsb|Rsb)\s*\]')
SIZE = re.compile(r'<size=([+]?(?:\d+(?:\.\d*)?|\.\d+))%>|</size>', re.I)


def tokens(text):
    value = text["text"] if isinstance(text, dict) else text
    rich = not isinstance(text, dict) or text.get("original_tmp", {}).get("m_isRichText", True)
    result = []
    index = 0
    styles = []
    horizontal_scale=1.0
    no_break=False
    size_scales=[]
    def append_character(char):
        style='+'.join(sorted(set(styles))) if styles else None
        if char=='\n':result.append(('char',char))
        elif char=='\t':result.append(('tab',char,style,horizontal_scale,no_break,size_scales[-1] if size_scales else 1))
        elif size_scales:result.append(('char',char,style,horizontal_scale,no_break,size_scales[-1]))
        elif no_break:result.append(('char',char,style,horizontal_scale,True))
        elif horizontal_scale!=1:result.append(('char',char,style,horizontal_scale))
        else:result.append(('char',char,style) if style else ('char',char))
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    while index < len(value):
        if rich and value[index] == '[':
            bracket = BRACKET.match(value, index)
            if bracket:
                # BaseTagProcessor converts these once to literal brackets;
                # the resulting glyph is not parsed as another MS command.
                append_character('[' if bracket[1] == 'Lsb' else ']')
                index = bracket.end()
                continue
            nb=NO_BREAK.match(value,index)
            if nb:
                no_break=not bool(nb[1]);index=nb.end();continue
            narrow=THREE_SPACE.match(value,index)
            if narrow:
                append_character(' ')
                index=narrow.end();continue
            ex=next((tag for tag in text.get('inline_ex',{}) if value.startswith(tag,index)),None) if isinstance(text,dict) else None
            if ex:
                if size_scales:raise ValueError('Original sized inline ex sprite is not yet recovered')
                style='+'.join(sorted(set(styles))) if styles else None
                result.extend([('space_em',-.01),('sprite',ex,style,horizontal_scale),('space_px',-.1*text['inline_ex'][ex]['font_size'])])
                index+=len(ex);continue
            element=next((tag for tag in text.get('inline_elements',{}) if value.startswith(tag,index)),None) if isinstance(text,dict) else None
            if element:
                if size_scales:raise ValueError('Original sized inline element is not yet recovered')
                if styles:raise ValueError('Original nested inline element/font command is not yet recovered')
                binding=text['inline_elements'][element]
                result.append(('char',binding['character'],binding['style'],horizontal_scale) if horizontal_scale!=1
                              else ('char',binding['character'],binding['style']));index+=len(element)
                continue
            match = FONT_COMMAND.match(value,index)
            if not match or not isinstance(text,dict) or match[2].lower() not in text.get('rich_styles',{}):
                raise ValueError('Original MS font command has no recovered binding: '+value[index:value.find(']',index)+1])
            if match[1]:
                if not styles or styles[-1]!=match[2].lower():raise ValueError('Unmatched original font command close')
                styles.pop()
            else:styles.append(match[2].lower())
            index=match.end()
        elif rich and value[index] == '<':
            sized=SIZE.match(value,index)
            if sized:
                if sized[1] is None:
                    if not size_scales:raise ValueError('Unmatched original TMP size close')
                    size_scales.pop()
                else:
                    scale=float(sized[1])/100
                    if scale<=0:raise ValueError('Invalid original TMP percentage size')
                    size_scales.append(scale)
                index=sized.end();continue
            layout=LAYOUT.match(value,index)
            if layout:
                kind=(layout[1] or layout[4]).lower()
                if kind=='line-indent':result.append(('line_indent_em' if (layout[3] or '').lower()=='em' else 'line_indent_px',float(layout[2])))
                elif kind=='/line-indent':result.append(('line_indent_px',0.0))
                elif kind=='indent':result.append(('indent_em' if (layout[3] or '').lower()=='em' else 'indent_px',float(layout[2]),None,1,False,size_scales[-1] if size_scales else 1))
                elif kind=='/indent':result.append(('indent_close',0.0))
                elif kind=='br':result.append(('char','\n'))
                elif kind=='/scale':horizontal_scale=1.0
                elif kind=='nobr':no_break=True
                elif kind=='/nobr':no_break=False
                elif kind=='scale':
                    if layout[3] or float(layout[2])<=0:raise ValueError('Invalid original TMP horizontal scale')
                    horizontal_scale=float(layout[2])
                index=layout.end();continue
            match = SPACE.match(value, index)
            if not match:
                raise ValueError("Unsupported original TMP rich-text command: " + value[index:value.find('>', index)+1])
            result.append(("space_em" if (match[2] or '').lower() == 'em' else "space_px", float(match[1])))
            index = match.end()
        else:
            append_character(value[index])
            index += 1
    if styles:raise ValueError('Unclosed original font command')
    if size_scales:raise ValueError('Unclosed original TMP size command')
    return result


def characters(text):
    return ''.join(t[1] for t in tokens(text) if t[0] in ('char','tab'))


def font_characters(text):
    """Yield the actual original font role for each visible character."""
    for token in tokens(text):
        if token[0]=='char':
            style=text['rich_styles'][token[2]] if len(token)>2 and token[2] else text
            yield style['font_id'],token[1]


def lines(text):
    result = [[]]
    for token in tokens(text):
        if token == ("char", "\n"):
            result.append([])
        else:
            result[-1].append(token)
    return result


def space_advance(token, font_size):
    return token[1] * font_size if token[0] == "space_em" else token[1]
