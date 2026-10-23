"""Source-bound font runs for recovered MS Bold/Italic and inline elements.

The native 0x4A1D780 formatter changes BOTH font and material. Runs use the
selected CardTextFontSettings text type 2, never synthetic bold or a system
font. This bounded layout shares the existing source-metric wrapping engine.
Italic uses the original TMP italicStyle geometry; element glyphs use the
stored Pokesymbol atlas and serialized point size, without enlarging a bitmap.
"""
from copy import deepcopy
from .text_layout import measure_text,_advance,token_spacing
from .text_tokens import space_advance


def expand_font_runs(text,fonts):
    """Return ordinary compiler fields placed on the shared mixed-font lines."""
    if not text.get('rich_styles') and not text.get('inline_ex') and '\t' not in text['text'] and not any(tag in text['text'].lower() for tag in ('<line-indent','<indent','<scale','<br>','<size=')):return [text]
    font=fonts[text['font_id']]
    measured=measure_text(text,font,fonts=fonts)
    size=measured['font_size'];spacing=measured['spacing']
    x,y,width,height=text['render_rect_top_left'];step=measured['line_step']
    count=len(measured['lines']);vertical=text.get('vertical_alignment','middle')
    baseline=y+height/2+(measured['ascent']+measured['descent'])/2-(count-1)*step/2
    if vertical=='top':baseline=y+measured['ascent']
    elif vertical=='bottom':baseline=y+height+measured['descent']-(count-1)*step
    baseline-=text.get('language_position_y_offset',0)
    fields=[]
    for line_index,line in enumerate(measured['lines']):
        line_width=measured['widths'][line_index];align=text['horizontal_alignment']
        pen=x+(width-line_width if align=='right' else (width-line_width)/2 if align=='center' else 0)
        line_start=pen
        justify=0
        if align=='justified' and not measured['paragraph_ends'][line_index]:
            spaces=sum(t[0]=='char' and t[1]==' ' for t in line)
            if spaces:justify=max(0,width-line_width)/spaces
        run=[];run_style=None;run_x=pen;run_width=0;run_horizontal=1;run_size_scale=1
        def flush():
            if not run:return
            field=deepcopy(text);field.pop('rich_styles',None)
            field.pop('inline_elements',None)
            field.pop('inline_ex',None)
            if run_style:field.update(deepcopy(text['rich_styles'][run_style]))
            run_size=text['rich_styles'][run_style].get('font_size',size) if run_style else size
            run_size*=run_size_scale
            ff=fonts[field['font_id']]['face_info'];scale=run_size/ff['m_PointSize']*ff['m_Scale']
            ascent=ff['m_AscentLine']*scale;descent=ff['m_DescentLine']*scale
            field.update(id=text['id']+'-run'+str(len(fields)),text=''.join(run),
                font_size=run_size,wrap=False,fit_font=False,horizontal_alignment='left',vertical_alignment='top',
                language_position_y_offset=0,
                render_rect_top_left=[run_x,baseline+line_index*step-ascent,run_width,ascent-descent],
                layout_engine='tmp-subset-v2',horizontal_scale=run_horizontal)
            field['rich_run_source']={'text_id':text['id'],'line':line_index,'style':run_style,
                'original_text':text['text'],'source_font_conditions':field.get('font_provenance'),
                'percentage_size_scale':run_size_scale,
                'mixed_line_width':line_width,'shared_baseline_down':baseline+line_index*step,
                'policy':'Original selected font/material and source glyph metrics; no raster resizing or synthetic bold.'}
            fields.append(field)
        for token in line:
            style=token[2] if len(token)>2 else None
            horizontal=token[3] if len(token)>3 else 1
            size_scale=token[5] if len(token)>5 else 1
            if token[0]=='sprite':
                from .inline_ex import sprite_geometry
                flush();run=[];run_width=0
                sprite_font=fonts[text['rich_styles'][style]['font_id']] if style else font
                geometry=sprite_geometry(token,text,sprite_font,size)
                fields.append({'id':text['id']+'-sprite'+str(len(fields)),'kind':'inline-sprite',
                    'inline_sprite':{'definition':text['inline_ex'][token[1]],'source_text':text['text'],
                        'source_text_id':text['id'],'current_font_role':text['rich_styles'][style]['font_id'] if style else text['font_id'],
                        'geometry':geometry,'line':line_index,'baseline_down':baseline+line_index*step},
                    'render_rect_top_left':[pen+geometry['bearing_x'],baseline+line_index*step-geometry['bearing_y'],geometry['width'],geometry['height']]})
                pen+=geometry['advance']+spacing;run_x=pen;run_style=None;run_horizontal=1
                continue
            if token[0]!='char':
                flush();run=[];run_width=0
                if token[0] in ('indent_px','position_px'):pen=line_start+token[1]
                elif token[0]!='indent_close':
                    pen+=(_advance(token,font,size,text,fonts,pen=pen-line_start)
                          if token[0]=='tab' else space_advance(token,size))
                run_x=pen;run_style=None
                continue
            if run and (style!=run_style or horizontal!=run_horizontal or size_scale!=run_size_scale):
                flush();run=[];run_width=0;run_x=pen
            if not run:run_style=style;run_x=pen;run_horizontal=horizontal;run_size_scale=size_scale
            advance=_advance(token,font,size,text,fonts)
            scaled_spacing=token_spacing(token,spacing)
            if run:run_width+=scaled_spacing
            run.append(token[1]);run_width+=advance
            pen+=advance+scaled_spacing+(justify if token[1]==' ' else 0)
            if justify and token[1]==' ':
                flush();run=[];run_width=0;run_x=pen
        flush()
    return fields
