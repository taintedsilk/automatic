import os
import math
import time
import typing
import torch
from compel.embeddings_provider import BaseTextualInversionManager, EmbeddingsProvider
from transformers import PreTrainedTokenizer
from modules import shared, prompt_parser, devices


debug = shared.log.trace if os.environ.get('SD_PROMPT_DEBUG', None) is not None else lambda *args, **kwargs: None
debug('Trace: PROMPT')
orig_encode_token_ids_to_embeddings = EmbeddingsProvider._encode_token_ids_to_embeddings # pylint: disable=protected-access


def compel_hijack(self, token_ids: torch.Tensor,
                  attention_mask: typing.Optional[torch.Tensor] = None) -> torch.Tensor:
    needs_hidden_states = self.returned_embeddings_type != 1
    text_encoder_output = self.text_encoder(token_ids, attention_mask, output_hidden_states=needs_hidden_states, return_dict=True)
    if not needs_hidden_states:
        return text_encoder_output.last_hidden_state
    try:
        normalized = self.returned_embeddings_type > 0
        clip_skip = math.floor(abs(self.returned_embeddings_type))
        interpolation = abs(self.returned_embeddings_type) - clip_skip
    except Exception:
        normalized = False
        clip_skip = 1
        interpolation = False
    if interpolation:
        hidden_state = (1 - interpolation) * text_encoder_output.hidden_states[-clip_skip] + interpolation * text_encoder_output.hidden_states[-(clip_skip+1)]
    else:
        hidden_state = text_encoder_output.hidden_states[-clip_skip]
    if normalized:
        hidden_state = self.text_encoder.text_model.final_layer_norm(hidden_state)
    return hidden_state


EmbeddingsProvider._encode_token_ids_to_embeddings = compel_hijack # pylint: disable=protected-access


# from https://github.com/damian0815/compel/blob/main/src/compel/diffusers_textual_inversion_manager.py
class DiffusersTextualInversionManager(BaseTextualInversionManager):
    def __init__(self, pipe, tokenizer):
        self.pipe = pipe
        self.tokenizer = tokenizer
        if hasattr(self.pipe, 'embedding_db'):
            self.pipe.embedding_db.embeddings_used.clear()

    # code from
    # https://github.com/huggingface/diffusers/blob/705c592ea98ba4e288d837b9cba2767623c78603/src/diffusers/loaders.py
    def maybe_convert_prompt(self, prompt: typing.Union[str, typing.List[str]], tokenizer: PreTrainedTokenizer):
        prompts = [prompt] if not isinstance(prompt, typing.List) else prompt
        prompts = [self._maybe_convert_prompt(p, tokenizer) for p in prompts]
        if not isinstance(prompt, typing.List):
            return prompts[0]
        return prompts

    def _maybe_convert_prompt(self, prompt: str, tokenizer: PreTrainedTokenizer):
        tokens = tokenizer.tokenize(prompt)
        unique_tokens = set(tokens)
        for token in unique_tokens:
            if token in tokenizer.added_tokens_encoder:
                if hasattr(self.pipe, 'embedding_db'):
                    self.pipe.embedding_db.embeddings_used.append(token)
                replacement = token
                i = 1
                while f"{token}_{i}" in tokenizer.added_tokens_encoder:
                    replacement += f" {token}_{i}"
                    i += 1
                prompt = prompt.replace(token, replacement)
        if hasattr(self.pipe, 'embedding_db'):
            self.pipe.embedding_db.embeddings_used = list(set(self.pipe.embedding_db.embeddings_used))
        debug(f'Prompt: convert={prompt}')
        return prompt

    def expand_textual_inversion_token_ids_if_necessary(self, token_ids: typing.List[int]) -> typing.List[int]:
        if len(token_ids) == 0:
            return token_ids
        prompt = self.pipe.tokenizer.decode(token_ids)
        prompt = self.maybe_convert_prompt(prompt, self.pipe.tokenizer)
        debug(f'Prompt: expand={prompt}')
        return self.pipe.tokenizer.encode(prompt, add_special_tokens=False)


def get_prompt_schedule(prompt, steps):
    t0 = time.time()
    temp = []
    schedule = prompt_parser.get_learned_conditioning_prompt_schedules([prompt], steps)[0]
    if all(x == schedule[0] for x in schedule):
        return [schedule[0][1]], False
    for chunk in schedule:
        for s in range(steps):
            if len(temp) < s + 1 <= chunk[0]:
                temp.append(chunk[1])
    debug(f'Prompt: schedule={temp} time={time.time() - t0}')
    return temp, len(schedule) > 1


def encode_prompts(pipe, p, prompts: list, negative_prompts: list, steps: int, clip_skip: typing.Optional[int] = None):
    if 'StableDiffusion' not in pipe.__class__.__name__ and 'DemoFusion':
        shared.log.warning(f"Prompt parser not supported: {pipe.__class__.__name__}")
        return None, None, None, None
    else:
        t0 = time.time()
        positive_schedule, scheduled = get_prompt_schedule(prompts[0], steps)
        negative_schedule, neg_scheduled = get_prompt_schedule(negative_prompts[0], steps)
        p.scheduled_prompt = scheduled or neg_scheduled
        p.prompt_embeds = []
        p.positive_pooleds = []
        p.negative_embeds = []
        p.negative_pooleds = []

        cache = {}
        for i in range(max(len(positive_schedule), len(negative_schedule))):
            positive_prompt = positive_schedule[i % len(positive_schedule)]
            negative_prompt = negative_schedule[i % len(negative_schedule)]
            results = cache.get(positive_prompt + negative_prompt, None)

            if results is None:
                results = get_weighted_text_embeddings(pipe, positive_prompt, negative_prompt, clip_skip)
                cache[positive_prompt + negative_prompt] = results

            prompt_embed, positive_pooled, negative_embed, negative_pooled = results
            if prompt_embed is not None:
                p.prompt_embeds.append(torch.cat([prompt_embed] * len(prompts), dim=0))
            if negative_embed is not None:
                p.negative_embeds.append(torch.cat([negative_embed] * len(negative_prompts), dim=0))
            if positive_pooled is not None:
                p.positive_pooleds.append(torch.cat([positive_pooled] * len(prompts), dim=0))
            if negative_pooled is not None:
                p.negative_pooleds.append(torch.cat([negative_pooled] * len(negative_prompts), dim=0))
        debug(f"Prompt Parser: Elapsed Time {time.time() - t0}")
        return


def get_prompts_with_weights(prompt: str):
    manager = DiffusersTextualInversionManager(shared.sd_model,
                                               shared.sd_model.tokenizer or shared.sd_model.tokenizer_2)
    prompt = manager.maybe_convert_prompt(prompt, shared.sd_model.tokenizer or shared.sd_model.tokenizer_2)
    texts_and_weights = prompt_parser.parse_prompt_attention(prompt)
    texts, text_weights = zip(*texts_and_weights)
    debug(f'Prompt: weights={texts_and_weights}')
    return texts, text_weights


def prepare_embedding_providers(pipe, clip_skip):
    device = pipe.device if str(pipe.device) != 'meta' else devices.device
    embeddings_providers = []
    if 'XL' in pipe.__class__.__name__:
        embedding_type = -(clip_skip + 1)
    else:
        embedding_type = clip_skip
    if getattr(pipe, "tokenizer", None) is not None and getattr(pipe, "text_encoder", None) is not None:
        provider = EmbeddingsProvider(tokenizer=pipe.tokenizer, text_encoder=pipe.text_encoder, truncate=False, returned_embeddings_type=embedding_type, device=device)
        embeddings_providers.append(provider)
    if getattr(pipe, "tokenizer_2", None) is not None and getattr(pipe, "text_encoder_2", None) is not None:
        provider = EmbeddingsProvider(tokenizer=pipe.tokenizer_2, text_encoder=pipe.text_encoder_2, truncate=False, returned_embeddings_type=embedding_type, device=device)
        embeddings_providers.append(provider)
    return embeddings_providers


def pad_to_same_length(pipe, embeds):
    if not hasattr(pipe, 'encode_prompt'):
        return embeds
    device = pipe.device if str(pipe.device) != 'meta' else devices.device
    try: # SDXL
        empty_embed = pipe.encode_prompt("")
    except TypeError:  # SD1.5
        empty_embed = pipe.encode_prompt("", device, 1, False)
    max_token_count = max([embed.shape[1] for embed in embeds])
    repeats = max_token_count - min([embed.shape[1] for embed in embeds])
    empty_batched = empty_embed[0].to(embeds[0].device).repeat(embeds[0].shape[0], repeats // empty_embed[0].shape[1], 1)
    for i, embed in enumerate(embeds):
        if embed.shape[1] < max_token_count:
            embed = torch.cat([embed, empty_batched], dim=1)
            embeds[i] = embed
    return embeds


def get_weighted_text_embeddings(pipe, prompt: str = "", neg_prompt: str = "", clip_skip: int = None):
    device = pipe.device if str(pipe.device) != 'meta' else devices.device
    prompt_2 = prompt.split("TE2:")[-1]
    neg_prompt_2 = neg_prompt.split("TE2:")[-1]
    prompt = prompt.split("TE2:")[0]
    neg_prompt = neg_prompt.split("TE2:")[0]

    ps = [get_prompts_with_weights(p) for p in [prompt, prompt_2]]
    positives, positive_weights = zip(*ps)
    ns = [get_prompts_with_weights(p) for p in [neg_prompt, neg_prompt_2]]
    negatives, negative_weights = zip(*ns)
    if hasattr(pipe, "tokenizer_2") and not hasattr(pipe, "tokenizer"):
        positives.pop(0)
        positive_weights.pop(0)
        negatives.pop(0)
        negative_weights.pop(0)

    embedding_providers = prepare_embedding_providers(pipe, clip_skip)
    prompt_embeds = []
    negative_prompt_embeds = []
    pooled_prompt_embeds = None
    negative_pooled_prompt_embeds = None
    for i in range(len(embedding_providers)):
        # add BREAK keyword that splits the prompt into multiple fragments
        text = list(positives[i])
        weights = list(positive_weights[i])
        text.append('BREAK')
        weights.append(-1)
        provider_embed = []
        while 'BREAK' in text:
            pos = text.index('BREAK')
            debug(f'Prompt: section="{text[:pos]}" len={len(text[:pos])} weights={weights[:pos]}')
            if len(text[:pos]) > 0:
                embed, ptokens = embedding_providers[i].get_embeddings_for_weighted_prompt_fragments(
                    text_batch=[text[:pos]], fragment_weights_batch=[weights[:pos]], device=device,
                    should_return_tokens=True)
                provider_embed.append(embed)
            text = text[pos + 1:]
            weights = weights[pos + 1:]
        prompt_embeds.append(torch.cat(provider_embed, dim=1))
        debug(f'Prompt: positive unpadded shape = {prompt_embeds[0].shape}')
        # negative prompt has no keywords
        embed, ntokens = embedding_providers[i].get_embeddings_for_weighted_prompt_fragments(text_batch=[negatives[i]],
                                                                                             fragment_weights_batch=[
                                                                                                 negative_weights[i]],
                                                                                             device=device,
                                                                                             should_return_tokens=True)
        negative_prompt_embeds.append(embed)

    if prompt_embeds[-1].shape[-1] > 768:
        if shared.opts.diffusers_pooled == "weighted":
            pooled_prompt_embeds = prompt_embeds[-1][
                torch.arange(prompt_embeds[-1].shape[0], device=device),
                (ptokens.to(dtype=torch.int, device=device) == 49407)
                .int()
                .argmax(dim=-1),
            ]
            negative_pooled_prompt_embeds = negative_prompt_embeds[-1][
                torch.arange(negative_prompt_embeds[-1].shape[0], device=device),
                (ntokens.to(dtype=torch.int, device=device) == 49407)
                .int()
                .argmax(dim=-1),
            ]
        else:
            try:
                pooled_prompt_embeds = embedding_providers[-1].get_pooled_embeddings(texts=[prompt_2], device=device) if prompt_embeds[-1].shape[-1] > 768 else None
                negative_pooled_prompt_embeds = embedding_providers[-1].get_pooled_embeddings(texts=[neg_prompt_2], device=device) if negative_prompt_embeds[-1].shape[-1] > 768 else None
            except Exception:
                pooled_prompt_embeds = None
                negative_pooled_prompt_embeds = None

    prompt_embeds = torch.cat(prompt_embeds, dim=-1) if len(prompt_embeds) > 1 else prompt_embeds[0]
    negative_prompt_embeds = torch.cat(negative_prompt_embeds, dim=-1) if len(negative_prompt_embeds) > 1 else \
        negative_prompt_embeds[0]
    debug(f'Prompt: shape={prompt_embeds.shape} negative={negative_prompt_embeds.shape}')
    if prompt_embeds.shape[1] != negative_prompt_embeds.shape[1]:
        [prompt_embeds, negative_prompt_embeds] = pad_to_same_length(pipe, [prompt_embeds, negative_prompt_embeds])
    return prompt_embeds, pooled_prompt_embeds, negative_prompt_embeds, negative_pooled_prompt_embeds
