import os
import time
from typing import List, Union
import cv2
import numpy as np
from PIL import Image
from modules.control import util # helper functions
from modules.control import unit # control units
from modules.control import processors # image preprocessors
from modules.control.units import controlnet # lllyasviel ControlNet
from modules.control.units import xs # VisLearn ControlNet-XS
from modules.control.units import lite # Kohya ControlLLLite
from modules.control.units import t2iadapter # TencentARC T2I-Adapter
from modules.control.units import reference # ControlNet-Reference
from modules import devices, shared, errors, processing, images, sd_models, scripts, masking
from modules.processing_class import StableDiffusionProcessingControl


debug = shared.log.trace if os.environ.get('SD_CONTROL_DEBUG', None) is not None else lambda *args, **kwargs: None
debug('Trace: CONTROL')
pipe = None
instance = None
original_pipeline = None


def restore_pipeline():
    global pipe, instance # pylint: disable=global-statement
    if instance is not None and hasattr(instance, 'restore'):
        instance.restore()
    if original_pipeline is not None and (original_pipeline.__class__.__name__ != shared.sd_model.__class__.__name__):
        shared.log.debug(f'Control restored pipeline: class={shared.sd_model.__class__.__name__} to={original_pipeline.__class__.__name__}')
        shared.sd_model = original_pipeline
    pipe = None
    instance = None
    devices.torch_gc()


def terminate(msg):
    restore_pipeline()
    shared.log.error(f'Control terminated: {msg}')
    return msg


def control_run(units: List[unit.Unit], inputs, inits, mask, unit_type: str, is_generator: bool, input_type: int,
                prompt, negative, styles, steps, sampler_index,
                seed, subseed, subseed_strength, seed_resize_from_h, seed_resize_from_w,
                cfg_scale, clip_skip, image_cfg_scale, diffusers_guidance_rescale, sag_scale, cfg_end, full_quality, restore_faces, tiling,
                hdr_mode, hdr_brightness, hdr_color, hdr_sharpen, hdr_clamp, hdr_boundary, hdr_threshold, hdr_maximize, hdr_max_center, hdr_max_boundry, hdr_color_picker, hdr_tint_ratio,
                resize_mode_before, resize_name_before, width_before, height_before, scale_by_before, selected_scale_tab_before,
                resize_mode_after, resize_name_after, width_after, height_after, scale_by_after, selected_scale_tab_after,
                resize_mode_mask, resize_name_mask, width_mask, height_mask, scale_by_mask, selected_scale_tab_mask,
                denoising_strength, batch_count, batch_size,
                enable_hr, hr_sampler_index, hr_denoising_strength, hr_upscaler, hr_force, hr_second_pass_steps, hr_scale, hr_resize_x, hr_resize_y, refiner_steps,
                refiner_start, refiner_prompt, refiner_negative,
                video_skip_frames, video_type, video_duration, video_loop, video_pad, video_interpolate,
                *input_script_args # pylint: disable=unused-argument
        ):
    global instance, pipe, original_pipeline # pylint: disable=global-statement
    debug(f'Control: type={unit_type} input={inputs} init={inits} type={input_type}')
    if inputs is None or (type(inputs) is list and len(inputs) == 0):
        inputs = [None]
    output_images: List[Image.Image] = [] # output images
    active_process: List[processors.Processor] = [] # all active preprocessors
    active_model: List[Union[controlnet.ControlNet, xs.ControlNetXS, t2iadapter.Adapter]] = [] # all active models
    active_strength: List[float] = [] # strength factors for all active models
    active_start: List[float] = [] # start step for all active models
    active_end: List[float] = [] # end step for all active models
    processed_image: Image.Image = None # last processed image
    if mask is not None and input_type == 0:
        input_type = 1 # inpaint always requires control_image

    p = StableDiffusionProcessingControl(
        prompt = prompt,
        negative_prompt = negative,
        styles = styles,
        steps = steps,
        n_iter = batch_count,
        batch_size = batch_size,
        sampler_name = processing.get_sampler_name(sampler_index),
        seed = seed,
        subseed = subseed,
        subseed_strength = subseed_strength,
        seed_resize_from_h = seed_resize_from_h,
        seed_resize_from_w = seed_resize_from_w,
        # advanced
        cfg_scale = cfg_scale,
        clip_skip = clip_skip,
        image_cfg_scale = image_cfg_scale,
        diffusers_guidance_rescale = diffusers_guidance_rescale,
        sag_scale = sag_scale,
        full_quality = full_quality,
        restore_faces = restore_faces,
        tiling = tiling,
        # resize
        resize_mode = resize_mode_before if resize_name_before != 'None' else 0,
        resize_name = resize_name_before,
        scale_by = scale_by_before,
        selected_scale_tab = selected_scale_tab_before,
        denoising_strength = denoising_strength,
        # inpaint
        inpaint_full_res = masking.opts.mask_only,
        inpainting_mask_invert = 1 if masking.opts.invert else 0,
        inpainting_fill = 1,
        # hdr
        hdr_mode=hdr_mode, hdr_brightness=hdr_brightness, hdr_color=hdr_color, hdr_sharpen=hdr_sharpen, hdr_clamp=hdr_clamp,
        hdr_boundary=hdr_boundary, hdr_threshold=hdr_threshold, hdr_maximize=hdr_maximize, hdr_max_center=hdr_max_center, hdr_max_boundry=hdr_max_boundry, hdr_color_picker=hdr_color_picker, hdr_tint_ratio=hdr_tint_ratio,
        # path
        outpath_samples=shared.opts.outdir_samples or shared.opts.outdir_control_samples,
        outpath_grids=shared.opts.outdir_grids or shared.opts.outdir_control_grids,
    )
    processing.process_init(p)
    # set initial resolution
    if resize_mode_before != 0 or inputs is None or inputs == [None]:
        p.width, p.height = width_before, height_before # pylint: disable=attribute-defined-outside-init
    else:
        del p.width
        del p.height
    # hires/refine defined outside of main init
    p.enable_hr = enable_hr
    p.hr_sampler_name = processing.get_sampler_name(hr_sampler_index)
    p.hr_denoising_strength = hr_denoising_strength
    p.hr_upscaler = hr_upscaler
    p.hr_force = hr_force
    p.hr_second_pass_steps = hr_second_pass_steps
    p.hr_scale = hr_scale
    p.hr_resize_x = hr_resize_x
    p.hr_resize_y = hr_resize_y
    p.refiner_steps = refiner_steps
    p.refiner_start = refiner_start
    p.refiner_prompt = refiner_prompt
    p.refiner_negative = refiner_negative
    if p.enable_hr and (p.hr_resize_x == 0 or p.hr_resize_y == 0):
        p.hr_upscale_to_x, p.hr_upscale_to_y = 8 * int(p.width * p.hr_scale / 8), 8 * int(p.height * p.hr_scale / 8)

    t0 = time.time()
    num_units = 0
    for u in units:
        if u.type != unit_type:
            continue
        num_units += 1
        debug(f'Control unit: i={num_units} type={u.type} enabled={u.enabled}')
        if not u.enabled:
            continue
        if unit_type == 't2i adapter' and u.adapter.model is not None:
            active_process.append(u.process)
            active_model.append(u.adapter)
            active_strength.append(float(u.strength))
            p.adapter_conditioning_factor = u.factor
            shared.log.debug(f'Control T2I-Adapter unit: i={num_units} process={u.process.processor_id} model={u.adapter.model_id} strength={u.strength} factor={u.factor}')
        elif unit_type == 'controlnet' and u.controlnet.model is not None:
            active_process.append(u.process)
            active_model.append(u.controlnet)
            active_strength.append(float(u.strength))
            active_start.append(float(u.start))
            active_end.append(float(u.end))
            p.guess_mode = u.guess
            shared.log.debug(f'Control ControlNet unit: i={num_units} process={u.process.processor_id} model={u.controlnet.model_id} strength={u.strength} guess={u.guess} start={u.start} end={u.end}')
        elif unit_type == 'xs' and u.controlnet.model is not None:
            active_process.append(u.process)
            active_model.append(u.controlnet)
            active_strength.append(float(u.strength))
            active_start.append(float(u.start))
            active_end.append(float(u.end))
            shared.log.debug(f'Control ControlNet-XS unit: i={num_units} process={u.process.processor_id} model={u.controlnet.model_id} strength={u.strength} guess={u.guess} start={u.start} end={u.end}')
        elif unit_type == 'lite' and u.controlnet.model is not None:
            active_process.append(u.process)
            active_model.append(u.controlnet)
            active_strength.append(float(u.strength))
            shared.log.debug(f'Control ControlLLite unit: i={num_units} process={u.process.processor_id} model={u.controlnet.model_id} strength={u.strength} guess={u.guess} start={u.start} end={u.end}')
        elif unit_type == 'reference':
            p.override = u.override
            p.attention = u.attention
            p.query_weight = float(u.query_weight)
            p.adain_weight = float(u.adain_weight)
            p.fidelity = u.fidelity
            shared.log.debug('Control Reference unit')
        else:
            if u.process.processor_id is not None:
                active_process.append(u.process)
            shared.log.debug(f'Control process unit: i={num_units} process={u.process.processor_id}')
            active_strength.append(float(u.strength))
    p.ops.append('control')
    debug(f'Control active: process={len(active_process)} model={len(active_model)}')

    has_models = False
    selected_models: List[Union[controlnet.ControlNetModel, xs.ControlNetXSModel, t2iadapter.AdapterModel]] = None
    control_conditioning = None
    control_guidance_start = None
    control_guidance_end = None
    if unit_type == 't2i adapter' or unit_type == 'controlnet' or unit_type == 'xs' or unit_type == 'lite':
        if len(active_model) == 0:
            selected_models = None
        elif len(active_model) == 1:
            selected_models = active_model[0].model if active_model[0].model is not None else None
            p.extra_generation_params["Control model"] = (active_model[0].model_id or '') if active_model[0].model is not None else None
            has_models = selected_models is not None
            control_conditioning = active_strength[0] if len(active_strength) > 0 else 1 # strength or list[strength]
            control_guidance_start = active_start[0] if len(active_start) > 0 else 0
            control_guidance_end = active_end[0] if len(active_end) > 0 else 1
        else:
            selected_models = [m.model for m in active_model if m.model is not None]
            p.extra_generation_params["Control model"] = ', '.join([(m.model_id or '') for m in active_model if m.model is not None])
            has_models = len(selected_models) > 0
            control_conditioning = active_strength[0] if len(active_strength) == 1 else list(active_strength) # strength or list[strength]
            control_guidance_start = active_start[0] if len(active_start) == 1 else list(active_start)
            control_guidance_end = active_end[0] if len(active_end) == 1 else list(active_end)
        p.extra_generation_params["Control conditioning"] = control_conditioning
    else:
        pass

    debug(f'Control: run type={unit_type} models={has_models}')
    if unit_type == 't2i adapter' and has_models:
        p.extra_generation_params["Control mode"] = 'T2I-Adapter'
        p.task_args['adapter_conditioning_scale'] = control_conditioning
        instance = t2iadapter.AdapterPipeline(selected_models, shared.sd_model)
        pipe = instance.pipeline
        if inits is not None:
            shared.log.warning('Control: T2I-Adapter does not support separate init image')
    elif unit_type == 'controlnet' and has_models:
        p.extra_generation_params["Control mode"] = 'ControlNet'
        p.task_args['controlnet_conditioning_scale'] = control_conditioning
        p.task_args['control_guidance_start'] = control_guidance_start
        p.task_args['control_guidance_end'] = control_guidance_end
        p.task_args['guess_mode'] = p.guess_mode
        instance = controlnet.ControlNetPipeline(selected_models, shared.sd_model)
        pipe = instance.pipeline
    elif unit_type == 'xs' and has_models:
        p.extra_generation_params["Control mode"] = 'ControlNet-XS'
        p.controlnet_conditioning_scale = control_conditioning
        p.control_guidance_start = control_guidance_start
        p.control_guidance_end = control_guidance_end
        instance = xs.ControlNetXSPipeline(selected_models, shared.sd_model)
        pipe = instance.pipeline
        if inits is not None:
            shared.log.warning('Control: ControlNet-XS does not support separate init image')
    elif unit_type == 'lite' and has_models:
        p.extra_generation_params["Control mode"] = 'ControlLLLite'
        p.controlnet_conditioning_scale = control_conditioning
        instance = lite.ControlLLitePipeline(shared.sd_model)
        pipe = instance.pipeline
        if inits is not None:
            shared.log.warning('Control: ControlLLLite does not support separate init image')
    elif unit_type == 'reference':
        p.extra_generation_params["Control mode"] = 'Reference'
        p.extra_generation_params["Control attention"] = p.attention
        p.task_args['reference_attn'] = 'Attention' in p.attention
        p.task_args['reference_adain'] = 'Adain' in p.attention
        p.task_args['attention_auto_machine_weight'] = p.query_weight
        p.task_args['gn_auto_machine_weight'] = p.adain_weight
        p.task_args['style_fidelity'] = p.fidelity
        instance = reference.ReferencePipeline(shared.sd_model)
        pipe = instance.pipeline
        if inits is not None:
            shared.log.warning('Control: ControlNet-XS does not support separate init image')
    else: # run in txt2img/img2img mode
        if len(active_strength) > 0:
            p.strength = active_strength[0]
        pipe = shared.sd_model
        instance = None
        """
        try:
            pipe = diffusers.AutoPipelineForText2Image.from_pipe(shared.sd_model) # use set_diffuser_pipe
        except Exception as e:
            shared.log.warning(f'Control pipeline create: {e}')
            pipe = shared.sd_model
        """


    debug(f'Control pipeline: class={pipe.__class__.__name__} args={vars(p)}')
    t1, t2, t3 = time.time(), 0, 0
    status = True
    frame = None
    video = None
    output_filename = None
    index = 0
    frames = 0

    # set pipeline
    if pipe.__class__.__name__ != shared.sd_model.__class__.__name__:
        original_pipeline = shared.sd_model
        shared.sd_model = pipe
        sd_models.move_model(shared.sd_model, shared.device)
        shared.sd_model.to(dtype=devices.dtype)
        debug(f'Control device={devices.device} dtype={devices.dtype}')
        sd_models.copy_diffuser_options(shared.sd_model, original_pipeline) # copy options from original pipeline
        sd_models.set_diffuser_options(shared.sd_model)
    else:
        original_pipeline = None

    try:
        with devices.inference_context():
            if isinstance(inputs, str): # only video, the rest is a list
                if input_type == 2: # separate init image
                    if isinstance(inits, str) and inits != inputs:
                        shared.log.warning('Control: separate init video not support for video input')
                        input_type = 1
                try:
                    video = cv2.VideoCapture(inputs)
                    if not video.isOpened():
                        yield terminate(f'Control: video open failed: path={inputs}')
                        return
                    frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
                    fps = int(video.get(cv2.CAP_PROP_FPS))
                    w, h = int(video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    codec = util.decode_fourcc(video.get(cv2.CAP_PROP_FOURCC))
                    status, frame = video.read()
                    if status:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    shared.log.debug(f'Control: input video: path={inputs} frames={frames} fps={fps} size={w}x{h} codec={codec}')
                except Exception as e:
                    yield terminate(f'Control: video open failed: path={inputs} {e}')
                    return

            while status:
                processed_image = None
                if frame is not None:
                    inputs = [Image.fromarray(frame)] # cv2 to pil
                for i, input_image in enumerate(inputs):
                    debug(f'Control Control image: {i + 1} of {len(inputs)}')
                    if shared.state.skipped:
                        shared.state.skipped = False
                        continue
                    if shared.state.interrupted:
                        shared.state.interrupted = False
                        yield terminate('Control interrupted')
                        return
                    # get input
                    if isinstance(input_image, str):
                        try:
                            input_image = Image.open(inputs[i])
                        except Exception as e:
                            shared.log.error(f'Control: image open failed: path={inputs[i]} type=control error={e}')
                            continue
                    # match init input
                    if input_type == 1:
                        debug('Control Init image: same as control')
                        init_image = input_image
                    elif inits is None:
                        debug('Control Init image: none')
                        init_image = None
                    elif isinstance(inits[i], str):
                        debug(f'Control: init image: {inits[i]}')
                        try:
                            init_image = Image.open(inits[i])
                        except Exception as e:
                            shared.log.error(f'Control: image open failed: path={inits[i]} type=init error={e}')
                            continue
                    else:
                        debug(f'Control Init image: {i % len(inits) + 1} of {len(inits)}')
                        init_image = inits[i % len(inits)]
                    index += 1
                    if video is not None and index % (video_skip_frames + 1) != 0:
                        continue

                    # resize before
                    if resize_mode_before != 0 and resize_name_before != 'None':
                        if selected_scale_tab_before == 1 and input_image is not None:
                            width_before, height_before = int(input_image.width * scale_by_before), int(input_image.height * scale_by_before)
                        if input_image is not None:
                            p.extra_generation_params["Control resize"] = f'{resize_name_before}'
                            debug(f'Control resize: op=before image={input_image} width={width_before} height={height_before} mode={resize_mode_before} name={resize_name_before}')
                            input_image = images.resize_image(resize_mode_before, input_image, width_before, height_before, resize_name_before)
                    if input_image is not None and init_image is not None and init_image.size != input_image.size:
                        debug(f'Control resize init: image={init_image} target={input_image}')
                        init_image = images.resize_image(resize_mode=1, im=init_image, width=input_image.width, height=input_image.height)
                    if input_image is not None and p.override is not None and p.override.size != input_image.size:
                        debug(f'Control resize override: image={p.override} target={input_image}')
                        p.override = images.resize_image(resize_mode=1, im=p.override, width=input_image.width, height=input_image.height)
                    if input_image is not None:
                        p.width = input_image.width
                        p.height = input_image.height
                        debug(f'Control: input image={input_image}')

                    processed_images = []
                    if mask is not None:
                        p.extra_generation_params["Mask only"] = masking.opts.mask_only if masking.opts.mask_only else None
                        p.extra_generation_params["Mask auto"] = masking.opts.auto_mask if masking.opts.auto_mask != 'None' else None
                        p.extra_generation_params["Mask invert"] = masking.opts.invert if masking.opts.invert else None
                        p.extra_generation_params["Mask blur"] = masking.opts.mask_blur if masking.opts.mask_blur > 0 else None
                        p.extra_generation_params["Mask erode"] = masking.opts.mask_erode if masking.opts.mask_erode > 0 else None
                        p.extra_generation_params["Mask dilate"] = masking.opts.mask_dilate if masking.opts.mask_dilate > 0 else None
                        p.extra_generation_params["Mask model"] = masking.opts.model if masking.opts.model is not None else None
                        masked_image = masking.run_mask(input_image=input_image, input_mask=mask, return_type='Masked', invert=p.inpainting_mask_invert==1) if mask is not None else input_image
                    else:
                        masked_image = input_image
                    for i, process in enumerate(active_process): # list[image]
                        debug(f'Control: i={i+1} process="{process.processor_id}" input={masked_image} override={process.override}')
                        processed_image = process(
                            image_input=masked_image,
                            mode='RGB',
                            resize_mode=resize_mode_before,
                            resize_name=resize_name_before,
                            scale_tab=selected_scale_tab_before,
                            scale_by=scale_by_before,
                        )
                        if processed_image is not None:
                            processed_images.append(processed_image)
                        if shared.opts.control_unload_processor and process.processor_id is not None:
                            processors.config[process.processor_id]['dirty'] = True # to force reload
                            process.model = None

                    debug(f'Control processed: {len(processed_images)}')
                    if len(processed_images) > 0:
                        p.extra_generation_params["Control process"] = [p.processor_id for p in active_process if p.processor_id is not None]
                        if len(p.extra_generation_params["Control process"]) == 0:
                            p.extra_generation_params["Control process"] = None
                        if any(img is None for img in processed_images):
                            yield terminate('Control: attempting process but output is none')
                            return
                        if len(processed_images) > 1:
                            processed_image = [np.array(i) for i in processed_images]
                            processed_image = util.blend(processed_image) # blend all processed images into one
                            processed_image = Image.fromarray(processed_image)
                        else:
                            processed_image = processed_images[0]
                        if isinstance(selected_models, list) and len(processed_images) == len(selected_models):
                            debug(f'Control: inputs match: input={len(processed_images)} models={len(selected_models)}')
                            p.init_images = processed_images
                        elif isinstance(selected_models, list) and len(processed_images) != len(selected_models):
                            yield terminate(f'Control: number of inputs does not match: input={len(processed_images)} models={len(selected_models)}')
                            return
                        elif selected_models is not None:
                            if len(processed_images) > 1:
                                debug('Control: using blended image for single model')
                            p.init_images = [processed_image]
                    else:
                        debug('Control processed: using input direct')
                        processed_image = input_image

                    if unit_type == 'reference':
                        p.ref_image = p.override or input_image
                        p.task_args.pop('image', None)
                        p.task_args['ref_image'] = p.ref_image
                        debug(f'Control: process=None image={p.ref_image}')
                        if p.ref_image is None:
                            yield terminate('Control: attempting reference mode but image is none')
                            return
                    elif unit_type == 'controlnet' and input_type == 1: # Init image same as control
                        p.task_args['control_image'] = p.init_images # switch image and control_image
                        p.task_args['strength'] = p.denoising_strength
                        p.init_images = [p.override or input_image] * len(active_model)
                    elif unit_type == 'controlnet' and input_type == 2: # Separate init image
                        if init_image is None:
                            shared.log.warning('Control: separate init image not provided')
                            init_image = input_image
                        p.task_args['control_image'] = p.init_images # switch image and control_image
                        p.task_args['strength'] = p.denoising_strength
                        p.init_images = [init_image] * len(active_model)

                    if is_generator:
                        image_txt = f'{processed_image.width}x{processed_image.height}' if processed_image is not None else 'None'
                        msg = f'process | {index} of {frames if video is not None else len(inputs)} | {"Image" if video is None else "Frame"} {image_txt}'
                        debug(f'Control yield: {msg}')
                        yield (None, processed_image, f'Control {msg}')
                    t2 += time.time() - t2

                    # determine txt2img, img2img, inpaint pipeline
                    if unit_type == 'reference': # special case
                        p.is_control = True
                        shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.TEXT_2_IMAGE)
                    elif not has_models: # run in txt2img/img2img/inpaint mode
                        if mask is not None:
                            p.task_args['strength'] = p.denoising_strength
                            p.image_mask = mask
                            p.init_images = [input_image]
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.INPAINTING)
                        elif processed_image is not None:
                            p.init_images = [processed_image]
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.IMAGE_2_IMAGE)
                        else:
                            p.init_hr(p.scale_by, p.resize_name, force=True)
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.TEXT_2_IMAGE)
                    elif has_models: # actual control
                        p.is_control = True
                        if mask is not None:
                            p.task_args['strength'] = denoising_strength
                            p.image_mask = mask
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.INPAINTING) # only controlnet supports inpaint
                        elif 'control_image' in p.task_args:
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.IMAGE_2_IMAGE) # only controlnet supports img2img
                        else:
                            shared.sd_model = sd_models.set_diffuser_pipe(shared.sd_model, sd_models.DiffusersTaskType.TEXT_2_IMAGE)
                            if hasattr(p, 'init_images') and p.init_images is not None:
                                p.task_args['image'] = p.init_images # need to set explicitly for txt2img
                                del p.init_images
                        if unit_type == 'lite':
                            p.init_image = [input_image]
                            instance.apply(selected_models, processed_image, control_conditioning)
                    if hasattr(p, 'init_images') and p.init_images is None: # delete empty
                        del p.init_images

                    # final check
                    if has_models:
                        if unit_type in ['controlnet', 't2i adapter', 'lite', 'xs'] and p.task_args.get('image', None) is None and getattr(p, 'init_images', None) is None:
                            yield terminate(f'Control: mode={p.extra_generation_params.get("Control mode", None)} input image is none')
                            return

                    # resize mask
                    if mask is not None and resize_mode_mask != 0 and resize_name_mask != 'None':
                        if selected_scale_tab_mask == 1:
                            width_mask, height_mask = int(input_image.width * scale_by_before), int(input_image.height * scale_by_before)
                        p.width, p.height = width_mask, height_mask
                        debug(f'Control resize: op=mask image={mask} width={width_mask} height={height_mask} mode={resize_mode_mask} name={resize_name_mask}')

                    # pipeline
                    output = None
                    if pipe is not None: # run new pipeline
                        pipe.restore_pipeline = restore_pipeline
                        debug(f'Control exec pipeline: task={sd_models.get_diffusers_task(pipe)} class={pipe.__class__}')
                        debug(f'Control exec pipeline: p={vars(p)}')
                        debug(f'Control exec pipeline: args={p.task_args} image={p.task_args.get("image", None)} control={p.task_args.get("control_image", None)} mask={p.task_args.get("mask_image", None) or p.image_mask} ref={p.task_args.get("ref_image", None)}')
                        if sd_models.get_diffusers_task(pipe) != sd_models.DiffusersTaskType.TEXT_2_IMAGE: # force vae back to gpu if not in txt2img mode
                            sd_models.move_model(pipe.vae, devices.device)
                        p.scripts = scripts.scripts_control
                        p.script_args = input_script_args
                        processed = p.scripts.run(p, *input_script_args)
                        if processed is None:
                            processed: processing.Processed = processing.process_images(p) # run actual pipeline
                        output = processed.images if processed is not None else None
                        # output = pipe(**vars(p)).images # alternative direct pipe exec call
                    else: # blend all processed images and return
                        output = [processed_image]
                    t3 += time.time() - t3

                    # outputs
                    output = output or []
                    for i, output_image in enumerate(output):
                        if output_image is not None:

                            # resize after
                            is_grid = len(output) == p.batch_size * p.n_iter + 1 and i == 0
                            if selected_scale_tab_after == 1:
                                width_after = int(output_image.width * scale_by_after)
                                height_after = int(output_image.height * scale_by_after)
                            if resize_mode_after != 0 and resize_name_after != 'None' and not is_grid:
                                debug(f'Control resize: op=after image={output_image} width={width_after} height={height_after} mode={resize_mode_after} name={resize_name_after}')
                                output_image = images.resize_image(resize_mode_after, output_image, width_after, height_after, resize_name_after)

                            output_images.append(output_image)
                            if shared.opts.include_mask:
                                if processed_image is not None and isinstance(processed_image, Image.Image):
                                    output_images.append(processed_image)

                            if is_generator:
                                image_txt = f'{output_image.width}x{output_image.height}' if output_image is not None else 'None'
                                if video is not None:
                                    msg = f'Control output | {index} of {frames} skip {video_skip_frames} | Frame {image_txt}'
                                else:
                                    msg = f'Control output | {index} of {len(inputs)} | Image {image_txt}'
                                yield (output_image, processed_image, msg) # result is control_output, proces_output

                if video is not None and frame is not None:
                    status, frame = video.read()
                    if status:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    debug(f'Control: video frame={index} frames={frames} status={status} skip={index % (video_skip_frames + 1)} progress={index/frames:.2f}')
                else:
                    status = False

            if video is not None:
                video.release()

            shared.log.info(f'Control: pipeline units={len(active_model)} process={len(active_process)} time={t3-t0:.2f} init={t1-t0:.2f} proc={t2-t1:.2f} ctrl={t3-t2:.2f} outputs={len(output_images)}')
    except Exception as e:
        shared.log.error(f'Control pipeline failed: type={unit_type} units={len(active_model)} error={e}')
        errors.display(e, 'Control')

    if len(output_images) == 0:
        output_images = None
        image_txt = 'images=None'
    else:
        image_str = [f'{image.width}x{image.height}' for image in output_images]
        image_txt = f'| Images {len(output_images)} | Size {" ".join(image_str)}'
        p.init_images = output_images # may be used for hires

    if video_type != 'None' and isinstance(output_images, list):
        p.do_not_save_grid = True # pylint: disable=attribute-defined-outside-init
        output_filename = images.save_video(p, filename=None, images=output_images, video_type=video_type, duration=video_duration, loop=video_loop, pad=video_pad, interpolate=video_interpolate, sync=True)
        image_txt = f'| Frames {len(output_images)} | Size {output_images[0].width}x{output_images[0].height}'

    image_txt += f' | {util.dict2str(p.extra_generation_params)}'
    restore_pipeline()
    debug(f'Control ready: {image_txt}')
    if is_generator:
        yield (output_images, processed_image, f'Control ready {image_txt}', output_filename)
    else:
        return (output_images, processed_image, f'Control ready {image_txt}', output_filename)
