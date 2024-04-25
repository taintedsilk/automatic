import gradio as gr
from modules import shared, modelloader, ui_symbols, ui_common, sd_samplers
from modules.ui_components import ToolButton


def create_toprow(is_img2img: bool = False, id_part: str = None):
    def apply_styles(prompt, prompt_neg, styles):
        prompt = shared.prompt_styles.apply_styles_to_prompt(prompt, styles)
        prompt_neg = shared.prompt_styles.apply_negative_styles_to_prompt(prompt_neg, styles)
        return [gr.Textbox.update(value=prompt), gr.Textbox.update(value=prompt_neg), gr.Dropdown.update(value=[])]


    def parse_style(styles):
        return styles.split('|') if styles is not None else None

    if id_part is None:
        id_part = "img2img" if is_img2img else "txt2img"
    with gr.Row(elem_id=f"{id_part}_toprow", variant="compact"):
        with gr.Column(elem_id=f"{id_part}_prompt_container", scale=6):
            with gr.Row():
                with gr.Column(scale=80):
                    with gr.Row():
                        prompt = gr.Textbox(elem_id=f"{id_part}_prompt", label="Prompt", show_label=False, lines=3, placeholder="Prompt", elem_classes=["prompt"])
            with gr.Row():
                with gr.Column(scale=80):
                    with gr.Row():
                        negative_prompt = gr.Textbox(elem_id=f"{id_part}_neg_prompt", label="Negative prompt", show_label=False, lines=3, placeholder="Negative prompt", elem_classes=["prompt"])
        with gr.Column(scale=1, elem_id=f"{id_part}_actions_column"):
            with gr.Row(elem_id=f"{id_part}_generate_box"):
                submit = gr.Button('Generate', elem_id=f"{id_part}_generate", variant='primary')
            with gr.Row(elem_id=f"{id_part}_generate_line2"):
                interrupt = gr.Button('Stop', elem_id=f"{id_part}_interrupt")
                interrupt.click(fn=lambda: shared.state.interrupt(), _js="requestInterrupt", inputs=[], outputs=[])
                skip = gr.Button('Skip', elem_id=f"{id_part}_skip")
                skip.click(fn=lambda: shared.state.skip(), inputs=[], outputs=[])
                pause = gr.Button('Pause', elem_id=f"{id_part}_pause")
                pause.click(fn=lambda: shared.state.pause(), _js='checkPaused', inputs=[], outputs=[])
            with gr.Row(elem_id=f"{id_part}_tools"):
                button_paste = gr.Button(value='Restore', variant='secondary', elem_id=f"{id_part}_paste") # symbols.paste
                button_clear = gr.Button(value='Clear', variant='secondary', elem_id=f"{id_part}_clear_prompt_btn") # symbols.clear
                button_extra = gr.Button(value='Networks', variant='secondary', elem_id=f"{id_part}_extra_networks_btn") # symbols.networks
                button_clear.click(fn=lambda *x: ['', ''], inputs=[prompt, negative_prompt], outputs=[prompt, negative_prompt], show_progress=False)
            with gr.Row(elem_id=f"{id_part}_counters"):
                token_counter = gr.HTML(value="<span>0/75</span>", elem_id=f"{id_part}_token_counter", elem_classes=["token-counter"])
                token_button = gr.Button(visible=False, elem_id=f"{id_part}_token_button")
                negative_token_counter = gr.HTML(value="<span>0/75</span>", elem_id=f"{id_part}_negative_token_counter", elem_classes=["token-counter"])
                negative_token_button = gr.Button(visible=False, elem_id=f"{id_part}_negative_token_button")
            with gr.Row(elem_id=f"{id_part}_styles_row"):
                styles = gr.Dropdown(label="Styles", elem_id=f"{id_part}_styles", choices=[style.name for style in shared.prompt_styles.styles.values()], value=[], multiselect=True)
                _styles_btn_refresh = ui_common.create_refresh_button(styles, shared.prompt_styles.reload, lambda: {"choices": [style.name for style in shared.prompt_styles.styles.values()]}, f"{id_part}_styles_refresh")
                styles_btn_select = gr.Button('Select', elem_id=f"{id_part}_styles_select", visible=False)
                styles_btn_select.click(_js="applyStyles", fn=parse_style, inputs=[styles], outputs=[styles])
                styles_btn_apply = ToolButton(ui_symbols.apply, elem_id=f"{id_part}_extra_apply", visible=False)
                styles_btn_apply.click(fn=apply_styles, inputs=[prompt, negative_prompt, styles], outputs=[prompt, negative_prompt, styles])
    return prompt, styles, negative_prompt, submit, button_paste, button_extra, token_counter, token_button, negative_token_counter, negative_token_button


def ar_change(ar, width, height):
    if ar == 'AR':
        return gr.update(interactive=True), gr.update(interactive=True)
    try:
        (w, h) = [float(x) for x in ar.split(':')]
    except Exception as e:
        shared.log.warning(f"Invalid aspect ratio: {ar} {e}")
        return gr.update(interactive=True), gr.update(interactive=True)
    if w > h:
        return gr.update(interactive=True, value=width), gr.update(interactive=False, value=int(width * h / w))
    elif w < h:
        return gr.update(interactive=False, value=int(height * w / h)), gr.update(interactive=True, value=height)
    else:
        return gr.update(interactive=True, value=width), gr.update(interactive=False, value=width)


def create_resolution_inputs(tab):
    width = gr.Slider(minimum=64, maximum=4096, step=8, label="Width", value=512, elem_id=f"{tab}_width")
    height = gr.Slider(minimum=64, maximum=4096, step=8, label="Height", value=512, elem_id=f"{tab}_height")
    ar_list = ['AR'] + [x.strip() for x in shared.opts.aspect_ratios.split(',') if x.strip() != '']
    ar_dropdown = gr.Dropdown(show_label=False, interactive=True, choices=ar_list, value=ar_list[0], elem_id=f"{tab}_ar", elem_classes=["ar-dropdown"])
    for c in [ar_dropdown, width, height]:
        c.change(fn=ar_change, inputs=[ar_dropdown, width, height], outputs=[width, height], show_progress=False)
    res_switch_btn = ToolButton(value=ui_symbols.switch, elem_id=f"{tab}_res_switch_btn", label="Switch dims")
    res_switch_btn.click(lambda w, h: (h, w), inputs=[width, height], outputs=[width, height], show_progress=False)
    return width, height


def create_interrogate_buttons(tab):
    button_interrogate = gr.Button(ui_symbols.int_clip, elem_id=f"{tab}_interrogate", elem_classes=['interrogate-clip'])
    button_deepbooru = gr.Button(ui_symbols.int_blip, elem_id=f"{tab}_deepbooru", elem_classes=['interrogate-blip'])
    return button_interrogate, button_deepbooru


def create_sampler_inputs(tab, accordion=True):
    with gr.Accordion(open=False, label="Sampler", elem_id=f"{tab}_sampler", elem_classes=["small-accordion"]) if accordion else gr.Group():
        with gr.Row(elem_id=f"{tab}_row_sampler"):
            sd_samplers.set_samplers()
            steps, sampler_index = create_sampler_and_steps_selection(sd_samplers.samplers, tab)
    return steps, sampler_index


def create_batch_inputs(tab):
    with gr.Accordion(open=False, label="Batch", elem_id=f"{tab}_batch", elem_classes=["small-accordion"]):
        with gr.Row(elem_id=f"{tab}_row_batch"):
            batch_count = gr.Slider(minimum=1, step=1, label='Batch count', value=1, elem_id=f"{tab}_batch_count")
            batch_size = gr.Slider(minimum=1, maximum=32, step=1, label='Batch size', value=1, elem_id=f"{tab}_batch_size")
    return batch_count, batch_size


def create_seed_inputs(tab, reuse_visible=True):
    with gr.Accordion(open=False, label="Seed", elem_id=f"{tab}_seed_group", elem_classes=["small-accordion"]):
        with gr.Row(elem_id=f"{tab}_seed_row", variant="compact"):
            seed = gr.Number(label='Initial seed', value=-1, elem_id=f"{tab}_seed", container=True)
            random_seed = ToolButton(ui_symbols.random, elem_id=f"{tab}_random_seed", label='Random seed')
            reuse_seed = ToolButton(ui_symbols.reuse, elem_id=f"{tab}_reuse_seed", label='Reuse seed', visible=reuse_visible)
        with gr.Row(elem_id=f"{tab}_subseed_row", variant="compact", visible=True):
            subseed = gr.Number(label='Variation', value=-1, elem_id=f"{tab}_subseed", container=True)
            random_subseed = ToolButton(ui_symbols.random, elem_id=f"{tab}_random_subseed")
            reuse_subseed = ToolButton(ui_symbols.reuse, elem_id=f"{tab}_reuse_subseed", visible=reuse_visible)
            subseed_strength = gr.Slider(label='Variation strength', value=0.0, minimum=0, maximum=1, step=0.01, elem_id=f"{tab}_subseed_strength")
        with gr.Row(visible=False):
            seed_resize_from_w = gr.Slider(minimum=0, maximum=4096, step=8, label="Resize seed from width", value=0, elem_id=f"{tab}_seed_resize_from_w")
            seed_resize_from_h = gr.Slider(minimum=0, maximum=4096, step=8, label="Resize seed from height", value=0, elem_id=f"{tab}_seed_resize_from_h")
        random_seed.click(fn=lambda: [-1, -1], show_progress=False, inputs=[], outputs=[seed, subseed])
        random_subseed.click(fn=lambda: -1, show_progress=False, inputs=[], outputs=[subseed])
    return seed, reuse_seed, subseed, reuse_subseed, subseed_strength, seed_resize_from_h, seed_resize_from_w


def create_advanced_inputs(tab):
    with gr.Accordion(open=False, label="Advanced", elem_id=f"{tab}_advanced", elem_classes=["small-accordion"]):
        with gr.Group():
            with gr.Row():
                cfg_scale = gr.Slider(minimum=0.0, maximum=30.0, step=0.1, label='CFG scale', value=6.0, elem_id=f"{tab}_cfg_scale")
                cfg_end = gr.Slider(minimum=0.0, maximum=1.0, step=0.1, label='CFG end', value=1.0, elem_id=f"{tab}_cfg_end")
            with gr.Row():
                image_cfg_scale = gr.Slider(minimum=0.0, maximum=30.0, step=0.1, label='Secondary guidance', value=6.0, elem_id=f"{tab}_image_cfg_scale")
                diffusers_guidance_rescale = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, label='Rescale guidance', value=0.7, elem_id=f"{tab}_image_cfg_rescale", visible=shared.backend == shared.Backend.DIFFUSERS)
                diffusers_sag_scale = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, label='Attention guidance', value=0.0, elem_id=f"{tab}_image_sag_scale", visible=shared.backend == shared.Backend.DIFFUSERS)
            with gr.Row():
                clip_skip = gr.Slider(label='CLIP skip', value=1, minimum=0, maximum=12, step=0.1, elem_id=f"{tab}_clip_skip", interactive=True)
        with gr.Group():
            gr.HTML('<br>')
            with gr.Row(elem_id=f"{tab}_advanced_options"):
                full_quality = gr.Checkbox(label='Full quality', value=True, elem_id=f"{tab}_full_quality")
                restore_faces = gr.Checkbox(label='Face restore', value=False, elem_id=f"{tab}_restore_faces")
                tiling = gr.Checkbox(label='Tiling', value=False, elem_id=f"{tab}_tiling", visible=True)
    return cfg_scale, clip_skip, image_cfg_scale, diffusers_guidance_rescale, diffusers_sag_scale, cfg_end, full_quality, restore_faces, tiling

def create_correction_inputs(tab):
    with gr.Accordion(open=False, label="Corrections", elem_id=f"{tab}_corrections", elem_classes=["small-accordion"], visible=shared.backend == shared.Backend.DIFFUSERS):
        with gr.Group(visible=shared.backend == shared.Backend.DIFFUSERS):
            with gr.Row(elem_id=f"{tab}_hdr_mode_row"):
                hdr_mode = gr.Dropdown(label="Mode", choices=["Relative values", "Absolute values"], type="index", value="Relative values", elem_id=f"{tab}_hdr_mode", show_label=False)
                gr.HTML('<br>')
            with gr.Row(elem_id=f"{tab}_correction_row"):
                hdr_brightness = gr.Slider(minimum=-1.0, maximum=1.0, step=0.1, value=0,  label='Brightness', elem_id=f"{tab}_hdr_brightness")
                hdr_sharpen = gr.Slider(minimum=-1.0, maximum=1.0, step=0.1, value=0,  label='Sharpen', elem_id=f"{tab}_hdr_sharpen")
                hdr_color = gr.Slider(minimum=0.0, maximum=4.0, step=0.1, value=0.0,  label='Color', elem_id=f"{tab}_hdr_color")
            with gr.Row(elem_id=f"{tab}_hdr_clamp_row"):
                hdr_clamp = gr.Checkbox(label='HDR clamp', value=False, elem_id=f"{tab}_hdr_clamp")
                hdr_boundary = gr.Slider(minimum=0.0, maximum=10.0, step=0.1, value=4.0,  label='Range', elem_id=f"{tab}_hdr_boundary")
                hdr_threshold = gr.Slider(minimum=0.0, maximum=1.0, step=0.01, value=0.95,  label='Threshold', elem_id=f"{tab}_hdr_threshold")
            with gr.Row(elem_id=f"{tab}_hdr_max_row"):
                hdr_maximize = gr.Checkbox(label='HDR maximize', value=False, elem_id=f"{tab}_hdr_maximize")
                hdr_max_center = gr.Slider(minimum=0.0, maximum=2.0, step=0.1, value=0.6,  label='Center', elem_id=f"{tab}_hdr_max_center")
                hdr_max_boundry = gr.Slider(minimum=0.5, maximum=2.0, step=0.1, value=1.0,  label='Max Range', elem_id=f"{tab}_hdr_max_boundry")
            with gr.Row(elem_id=f"{tab}_hdr_color_row"):
                hdr_color_picker = gr.ColorPicker(label="Color", show_label=True, container=False, value=None, elem_id=f"{tab}_hdr_color_picker")
                hdr_tint_ratio = gr.Slider(label='Color grading', minimum=-1.0, maximum=1.0, step=0.05, value=0.0, elem_id=f"{tab}_hdr_tint_ratio")
        return hdr_mode, hdr_brightness, hdr_color, hdr_sharpen, hdr_clamp, hdr_boundary, hdr_threshold, hdr_maximize, hdr_max_center, hdr_max_boundry, hdr_color_picker, hdr_tint_ratio,


def create_sampler_and_steps_selection(choices, tabname):
    def set_sampler_original_options(sampler_options, sampler_algo):
        shared.opts.data['schedulers_brownian_noise'] = 'brownian noise' in sampler_options
        shared.opts.data['schedulers_discard_penultimate'] = 'discard penultimate sigma' in sampler_options
        shared.opts.data['schedulers_sigma'] = sampler_algo
        shared.opts.save(shared.config_filename, silent=True)

    def set_sampler_diffuser_options(sampler_options):
        shared.opts.data['schedulers_use_karras'] = 'karras' in sampler_options
        shared.opts.data['schedulers_use_thresholding'] = 'dynamic thresholding' in sampler_options
        shared.opts.data['schedulers_use_loworder'] = 'low order' in sampler_options
        shared.opts.data['schedulers_rescale_betas'] = 'rescale beta' in sampler_options
        shared.opts.save(shared.config_filename, silent=True)

    with gr.Row(elem_classes=['flex-break']):
        sampler_index = gr.Dropdown(label='Sampling method', elem_id=f"{tabname}_sampling", choices=[x.name for x in choices], value='Default', type="index")
        steps = gr.Slider(minimum=1, maximum=99, step=1, label="Sampling steps", elem_id=f"{tabname}_steps", value=20)
    if shared.backend == shared.Backend.ORIGINAL:
        with gr.Row(elem_classes=['flex-break']):
            choices = ['brownian noise', 'discard penultimate sigma']
            values = []
            values += ['brownian noise'] if shared.opts.data.get('schedulers_brownian_noise', False) else []
            values += ['discard penultimate sigma'] if shared.opts.data.get('schedulers_discard_penultimate', True) else []
            sampler_options = gr.CheckboxGroup(label='Sampler options', elem_id=f"{tabname}_sampler_options", choices=choices, value=values, type='value')
        with gr.Row(elem_classes=['flex-break']):
            shared.opts.data['schedulers_sigma'] = shared.opts.data.get('schedulers_sigma', 'default')
            sampler_algo = gr.Radio(label='Sigma algorithm', elem_id=f"{tabname}_sigma_algo", choices=['default', 'karras', 'exponential', 'polyexponential'], value=shared.opts.data['schedulers_sigma'], type='value')
        sampler_options.change(fn=set_sampler_original_options, inputs=[sampler_options, sampler_algo], outputs=[])
        sampler_algo.change(fn=set_sampler_original_options, inputs=[sampler_options, sampler_algo], outputs=[])
    else:
        with gr.Row(elem_classes=['flex-break']):
            choices = ['karras', 'dynamic threshold', 'low order', 'rescale beta']
            values = []
            values += ['karras'] if shared.opts.data.get('schedulers_use_karras', True) else []
            values += ['dynamic threshold'] if shared.opts.data.get('schedulers_use_thresholding', False) else []
            values += ['low order'] if shared.opts.data.get('schedulers_use_loworder', True) else []
            values += ['rescale beta'] if shared.opts.data.get('schedulers_rescale_betas', False) else []
            sampler_options = gr.CheckboxGroup(label='Sampler options', elem_id=f"{tabname}_sampler_options", choices=choices, value=values, type='value')
        sampler_options.change(fn=set_sampler_diffuser_options, inputs=[sampler_options], outputs=[])
    return steps, sampler_index


def create_hires_inputs(tab):
    with gr.Accordion(open=False, label="Refine", elem_id=f"{tab}_second_pass", elem_classes=["small-accordion"]):
        with gr.Group():
            with gr.Row(elem_id=f"{tab}_hires_row1"):
                enable_hr = gr.Checkbox(label='Enable second pass', value=False, elem_id=f"{tab}_enable_hr")
            with gr.Row(elem_id=f"{tab}_hires_fix_row1", variant="compact"):
                hr_upscaler = gr.Dropdown(label="Upscaler", elem_id=f"{tab}_hr_upscaler", choices=[*shared.latent_upscale_modes, *[x.name for x in shared.sd_upscalers]], value=shared.latent_upscale_default_mode)
                hr_scale = gr.Slider(minimum=0.1, maximum=8.0, step=0.05, label="Rescale by", value=2.0, elem_id=f"{tab}_hr_scale")
            with gr.Row(elem_id=f"{tab}_hires_fix_row3", variant="compact"):
                hr_resize_x = gr.Slider(minimum=0, maximum=4096, step=8, label="Width resize", value=0, elem_id=f"{tab}_hr_resize_x")
                hr_resize_y = gr.Slider(minimum=0, maximum=4096, step=8, label="Height resize", value=0, elem_id=f"{tab}_hr_resize_y")
            with gr.Row(elem_id=f"{tab}_hires_fix_row2", variant="compact"):
                hr_force = gr.Checkbox(label='Force HiRes', value=False, elem_id=f"{tab}_hr_force")
                hr_sampler_index = gr.Dropdown(label='Secondary sampler', elem_id=f"{tab}_sampling_alt", choices=[x.name for x in sd_samplers.samplers], value='Default', type="index")
            with gr.Row(elem_id=f"{tab}_hires_row2"):
                hr_second_pass_steps = gr.Slider(minimum=0, maximum=99, step=1, label='HiRes steps', elem_id=f"{tab}_steps_alt", value=20)
                denoising_strength = gr.Slider(minimum=0.0, maximum=0.99, step=0.01, label='Strength', value=0.3, elem_id=f"{tab}_denoising_strength")
        with gr.Group(visible=shared.backend == shared.Backend.DIFFUSERS):
            with gr.Row(elem_id=f"{tab}_refiner_row1", variant="compact"):
                refiner_start = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, label='Refiner start', value=0.0, elem_id=f"{tab}_refiner_start")
                refiner_steps = gr.Slider(minimum=0, maximum=99, step=1, label="Refiner steps", elem_id=f"{tab}_refiner_steps", value=10)
            with gr.Row(elem_id=f"{tab}_refiner_row3", variant="compact"):
                refiner_prompt = gr.Textbox(value='', label='Secondary prompt', elem_id=f"{tab}_refiner_prompt")
            with gr.Row(elem_id="txt2img_refiner_row4", variant="compact"):
                refiner_negative = gr.Textbox(value='', label='Secondary negative prompt', elem_id=f"{tab}_refiner_neg_prompt")
    return enable_hr, hr_sampler_index, denoising_strength, hr_upscaler, hr_force, hr_second_pass_steps, hr_scale, hr_resize_x, hr_resize_y, refiner_steps, refiner_start, refiner_prompt, refiner_negative


def create_resize_inputs(tab, images, accordion=True, latent=False):
    dummy_component = gr.Number(visible=False, value=0)
    with gr.Accordion(open=False, label="Resize", elem_classes=["small-accordion"], elem_id=f"{tab}_resize_group") if accordion else gr.Group():
        # with gr.Row():
        #    resize_mode = gr.Radio(label="Mode", elem_id=f"{tab}_resize_mode", choices=shared.resize_modes, type="index", value='Fixed')
        with gr.Row():
            resize_mode = gr.Dropdown(label="Mode", elem_id=f"{tab}_resize_mode", choices=shared.resize_modes, type="index", value='Fixed')
            resize_name = gr.Dropdown(label="Method", elem_id=f"{tab}_resize_name", choices=([] if not latent else list(shared.latent_upscale_modes)) + [x.name for x in shared.sd_upscalers], value=shared.latent_upscale_default_mode)
            ui_common.create_refresh_button(resize_name, modelloader.load_upscalers, lambda: {"choices": modelloader.load_upscalers()}, 'refresh_upscalers')

        with gr.Row(visible=True) as _resize_group:
            with gr.Column(elem_id=f"{tab}_column_size"):
                selected_scale_tab = gr.State(value=0) # pylint: disable=abstract-class-instantiated
                with gr.Tabs():
                    with gr.Tab(label="Fixed") as tab_scale_to:
                        with gr.Row():
                            with gr.Column(elem_id=f"{tab}_column_size"):
                                with gr.Row():
                                    width = gr.Slider(minimum=64, maximum=8192, step=8, label="Width", value=512, elem_id=f"{tab}_width")
                                    height = gr.Slider(minimum=64, maximum=8192, step=8, label="Height", value=512, elem_id=f"{tab}_height")
                                    ar_list = ['AR'] + [x.strip() for x in shared.opts.aspect_ratios.split(',') if x.strip() != '']
                                    ar_dropdown = gr.Dropdown(show_label=False, interactive=True, choices=ar_list, value=ar_list[0], elem_id=f"{tab}_ar", elem_classes=["ar-dropdown"])
                                    for c in [ar_dropdown, width, height]:
                                        c.change(fn=ar_change, inputs=[ar_dropdown, width, height], outputs=[width, height], show_progress=False)
                                    res_switch_btn = ToolButton(value=ui_symbols.switch, elem_id=f"{tab}_res_switch_btn")
                                    res_switch_btn.click(lambda w, h: (h, w), inputs=[width, height], outputs=[width, height], show_progress=False)
                                    detect_image_size_btn = ToolButton(value=ui_symbols.detect, elem_id=f"{tab}_detect_image_size_btn")
                                    detect_image_size_btn.click(fn=lambda w, h, _: (w or gr.update(), h or gr.update()), _js=f'currentImageResolution{tab}', inputs=[dummy_component, dummy_component, dummy_component], outputs=[width, height], show_progress=False)
                    with gr.Tab(label="Scale") as tab_scale_by:
                        scale_by = gr.Slider(minimum=0.05, maximum=8.0, step=0.05, label="Scale", value=1.0, elem_id=f"{tab}_scale")
                    for component in images:
                        component.change(fn=lambda: None, _js="updateImg2imgResizeToTextAfterChangingImage", inputs=[], outputs=[], show_progress=False)
            tab_scale_to.select(fn=lambda: 0, inputs=[], outputs=[selected_scale_tab])
            tab_scale_by.select(fn=lambda: 1, inputs=[], outputs=[selected_scale_tab])
            # resize_mode.change(fn=lambda x: gr.update(visible=x != 0), inputs=[resize_mode], outputs=[_resize_group])
    return resize_mode, resize_name, width, height, scale_by, selected_scale_tab
