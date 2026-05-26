# 2D Spatial Awareness in Transformers: Reading Notes

Scope: this is a synthesis of the papers you listed, organized around what each one teaches about 2D geometry, layout reasoning, and generation. I grouped them by mechanism because the field is not one method; it is several overlapping approaches.

## How to read this

- `Understanding` papers: encode 2D position into a transformer that reads a layout.
- `Positional bias` papers: improve the model's notion of 2D distance, direction, or structure.
- `Generation` papers: produce or refine layouts, placements, or spatial designs.
- `Agent` papers: push an LLM or transformer toward explicit spatial reasoning.

## Best papers for schematic or KiCad-style geometry

If the target is a schematic/layout agent, the most transferable ideas are:

1. `LayoutLMv2` and `LayoutLMv3` for text+geometry grounding.
2. `LayoutTransformer`, `BLT`, `LayoutDM`, `RALF`, and `LayoutDiT` for actual layout generation and refinement.
3. `iRPE`, `2D RoPE`, `Spiral RoPE`, and `GeoPE` for 2D spatial inductive bias.
4. `DocFormer` and `DocFormerv2` for multimodal local alignment.
5. `LaySPA` for explicit spatial reasoning in an LLM-style agent.

---

## 1) Document / layout understanding foundations

### LayoutLM (2019)

- Core idea: early large-scale transformer for jointly modeling text and 2D document layout.
- Spatial mechanism: OCR token bounding boxes are embedded as 2D coordinates, not just token order.
- What it teaches: if geometry matters, attach coordinates to tokens from the start.
- Relevance to schematics: useful for labels, annotations, net names, and any token that lives near a symbol or wire.
- Source: https://www.microsoft.com/en-us/research/publication/layoutlm-pre-training-of-text-and-layout-for-document-image-understanding/

### LayoutLMv2 (2020)

- Core idea: tight multimodal pretraining for text, image, and layout together.
- Spatial mechanism: spatial-aware self-attention over relative positions, plus image-text fusion.
- What it teaches: relative relations often matter more than absolute page coordinates.
- Relevance to schematics: strong baseline when proximity, adjacency, and attachment are the key signal.
- Source: https://www.microsoft.com/en-us/research/publication/layoutlmv2-multi-modal-pre-training-for-visually-rich-document-understanding/

### LayoutLMv3 (2022)

- Core idea: simplify document pretraining by unifying text and image masking in one backbone.
- Spatial mechanism: word-patch alignment and unified masking, rather than separate heavy visual machinery.
- What it teaches: cleaner objectives can beat architectural complexity.
- Relevance to schematics: useful when OCR, rendered geometry, and labels must align in one model.
- Source: https://www.microsoft.com/en-us/research/publication/layoutlmv3-pre-training-for-document-ai-with-unified-text-and-image-masking/

### DocFormer (2021)

- Core idea: end-to-end multimodal document transformer.
- Spatial mechanism: shared spatial embeddings across text and vision, with multimodal self-attention.
- What it teaches: fuse OCR and pixels in the same spatial frame.
- Relevance to schematics: helpful when the same object must be recognized from text and from the rendered drawing.
- Source: https://www.amazon.science/publications/docformer-end-to-end-transformer-for-document-understanding

### LayoutTransformer: Layout Generation and Completion with Self-attention (2020)

- Core idea: treat layout as a set-generation problem, not just an encoding problem.
- Spatial mechanism: self-attention over layout primitives such as boxes, classes, and coordinates.
- What it teaches: layout can be generated or completed by reasoning over pairwise relations.
- Relevance to schematics: one of the most directly relevant papers for symbol placement, completion, and insertion tasks.
- Source: https://openaccess.thecvf.com/content/ICCV2021/html/Gupta_LayoutTransformer_Layout_Generation_and_Completion_With_Self-Attention_ICCV_2021_paper.html

### DiT: Self-supervised Pre-training for Document Image Transformer (2022)

- Core idea: self-supervised document-image transformer pretraining without relying on OCR.
- Spatial mechanism: learns layout structure from patch-level visual evidence.
- What it teaches: vision backbones can learn layout priors directly from images.
- Relevance to schematics: useful if OCR is missing, weak, or noisy; also useful for rendered schematic understanding.
- Source: https://www.microsoft.com/en-us/research/publication/dit-self-supervised-pre-training-for-document-image-transformer/

### DocFormerv2 (2023)

- Core idea: follow-up to DocFormer with stronger local multimodal alignment.
- Spatial mechanism: encoder-decoder setup with local feature alignment tasks.
- What it teaches: local binding is critical when small offsets change meaning.
- Relevance to schematics: especially useful for connecting a label to the exact pin, wire, or symbol it belongs to.
- Source: https://www.amazon.science/publications/docformerv2-local-features-for-document-understanding

### LayoutLLM (2024)

- Core idea: layout instruction tuning for document understanding with LLMs / MLLMs.
- Spatial mechanism: layout-aware pretraining, region-level supervision, and LayoutCoT-style grounding.
- What it teaches: instruction following only works well after spatial grounding exists.
- Relevance to schematics: important bridge between classic layout encoders and agentic layout assistants.
- Source: https://huggingface.co/papers/2404.05225

### Transformers and Language Models in Form Understanding (2024 survey)

- Core idea: survey of the document/form understanding literature.
- Spatial mechanism: not a new method; it maps how geometry, OCR, fusion, and datasets fit together.
- What it teaches: the field splits into token labeling, key information extraction, VQA, and layout analysis.
- Relevance to schematics: useful as a map of the problem space before choosing a modeling strategy.
- Source: https://link.springer.com/article/10.1007/s10462-024-11000-0

---

## 2) Positional encoding and 2D spatial bias

### iRPE: Rethinking and Improving Relative Position Encoding for Vision Transformer

- Core idea: replace generic 1D relative position encoding with image-specific 2D relative position encoding.
- Spatial mechanism: direction-aware distances, bias mode vs contextual mode, and a piecewise index function.
- What it teaches: 2D relations need more than a naive 1D extension.
- Relevance to schematics: very strong baseline for placement, adjacency, and geometry-aware attention.
- Source: https://arxiv.org/abs/2107.14222

### Rotary Position Embedding for Vision Transformer (2D RoPE / RoPE-ViT)

- Core idea: extend RoPE from language to 2D vision.
- Spatial mechanism: axial RoPE splits x and y, while mixed learnable frequencies help represent diagonal relations.
- What it teaches: rotary position works well for extrapolation and generalization across resolutions.
- Relevance to schematics: a strong default for 2D token streams, especially when resolution changes.
- Source: https://arxiv.org/abs/2403.13298

### 2D-TPE: Two-Dimensional Positional Encoding Enhances Table Understanding for Large Language Models

- Core idea: preserve 2D table structure while keeping attention close to standard transformer machinery.
- Spatial mechanism: heads choose traversal orders such as row-wise or column-wise, with a router predicting the order.
- What it teaches: when structure is grid-like, dynamic traversal can preserve 2D relations better than flattening.
- Relevance to schematics: good for tables, grids, or serialized structured layouts; less general than RoPE or iRPE.
- Source: https://arxiv.org/abs/2409.19700

### A 2D Semantic-Aware Position Encoding for Vision Transformers (SaPE^2)

- Core idea: position should depend partly on content, not only coordinates.
- Spatial mechanism: semantic similarity and local content contribute to the positional signal.
- What it teaches: content-aware grouping can help when repeated or similar patches should interact.
- Relevance to schematics: useful for repeated symbols or subcircuits, but can be too content-heavy if exact geometry matters most.
- Source: https://arxiv.org/abs/2505.09466

### Boosting Resolution Generalization of Diffusion Transformers with 2D Randomized Positional Encodings (RPE-2D)

- Core idea: make diffusion transformers robust to higher-resolution inference.
- Spatial mechanism: randomize x/y positions during training so inference positions are less out-of-distribution.
- What it teaches: positional distribution shift is a major failure mode for generative spatial models.
- Relevance to schematics: useful for layout synthesis when train and test canvas sizes differ.
- Source: https://arxiv.org/abs/2503.18719

### Rotate Your Rotary Positional Embeddings in the 2D Plane

- Core idea: fix the axis bias in standard 2D RoPE.
- Spatial mechanism: partition channels into multiple direction groups and rotate each group along a chosen direction.
- What it teaches: pure x/y encoding still under-models diagonal and oblique relations.
- Relevance to schematics: especially attractive when geometry is not purely axis-aligned.
- Source: https://arxiv.org/abs/2602.03227

### 2-D SSM: A General Spatial Layer for Visual Transformers

- Core idea: add an explicit 2D spatial layer rather than only a positional encoding.
- Spatial mechanism: a multidimensional state-space layer models horizontal and vertical state separately.
- What it teaches: architectural spatial bias can be stronger than a PE tweak.
- Relevance to schematics: interesting if you want local structure and translation bias without hand-crafted coordinate features.
- Source: https://arxiv.org/abs/2306.06635

### Learning Spatial Decay for Vision Transformers

- Core idea: make distance-based decay learnable and content-dependent.
- Spatial mechanism: combines content relevance with Manhattan-distance priors via a gating mechanism.
- What it teaches: far-away patches should not always be downweighted the same way.
- Relevance to schematics: useful when both distance and semantic relation matter, such as in component blocks and grouped subcircuits.
- Source: https://arxiv.org/abs/2508.09525

### Positional Encoding Field (PE-Field)

- Core idea: extend positional encoding into a structured field, with hierarchical control.
- Spatial mechanism: 3D field-style encoding plus depth-aware and sub-patch encodings.
- What it teaches: PEs can be made hierarchical and geometry-aware, not just index-based.
- Relevance to schematics: more generation-oriented, but interesting for multi-resolution layout editing.
- Source: https://arxiv.org/abs/2510.20385

### GeoPE: A Unified Geometric Positional Embedding for Vision Transformers

- Core idea: make positional embedding more geometrically principled.
- Spatial mechanism: quaternion-space rotation, symmetric operator construction, and a linear relative variant.
- What it teaches: flattening 2D to 1D creates false neighbors; a better geometric representation can avoid axis-order bias.
- Relevance to schematics: one of the strongest candidates in the list for true 2D structure rather than weak axis-wise offsets.
- Source: https://arxiv.org/abs/2512.04963

---

## 3) Layout generation, placement, and design synthesis

### BLT: Bidirectional Layout Transformer for Controllable Layout Generation

- Core idea: generate a draft layout and iteratively refine low-confidence elements.
- Geometry: standard layout attributes, with masked items chosen for revision.
- Training signal: masked attribute prediction and hierarchical masking.
- What it teaches: layout generation does not need to be left-to-right only.
- Relevance to schematics: good template for improving an initial schematic instead of generating everything from scratch.
- Source: https://arxiv.org/abs/2112.05112

### LayoutDM: Transformer-based Diffusion Model for Layout Generation

- Core idea: use diffusion to generate layouts.
- Geometry: layout elements are treated as structured attributes and boxes inside the diffusion process.
- Training signal: standard conditional denoising objective.
- What it teaches: probabilistic recovery of plausible placements gives better diversity than a plain autoregressive decoder.
- Relevance to schematics: useful when you want robust placement with more than one plausible answer.
- Source: https://arxiv.org/abs/2305.02567

### RALF: Retrieval-Augmented Layout Transformer for Content-Aware Layout Generation

- Core idea: condition generation on retrieved nearest-neighbor layout examples.
- Geometry: usual layout structure, but guided by exemplar layouts that act as spatial priors.
- Training signal: autoregressive generation with retrieved examples in context.
- What it teaches: retrieval is a practical way to inject style and structure when data is sparse.
- Relevance to schematics: very useful when symbol placement patterns repeat across similar circuit styles.
- Source: https://arxiv.org/abs/2311.13602

### LayoutPrompter

- Core idea: training-free layout generation via prompting and in-context learning.
- Geometry: serialized layouts in text form.
- Training signal: none; relies on prompt construction and ranking.
- What it teaches: a prompt-only baseline is worth trying before fine-tuning.
- Relevance to schematics: useful if you want a cheap, fast prototype for structure generation or comparison.
- Source: https://arxiv.org/abs/2311.06495

### LayoutDiT: Exploring Content-Graphic Balance in Layout Generation with Diffusion Transformer

- Core idea: balance content understanding with graphic structure in a diffusion transformer.
- Geometry: saliency bounding-box constraints and adaptive content-vs-graphic emphasis.
- Training signal: diffusion objective plus explicit graphic constraints.
- What it teaches: good layouts come from balancing aesthetics and feasibility.
- Relevance to schematics: useful if the agent must respect both visual saliency and compositional balance.
- Source: https://arxiv.org/abs/2407.15233

### LayoutDETR: Detection Transformer Is a Good Multimodal Layout Designer

- Core idea: reframe layout generation as detection.
- Geometry: predicts object locations, scales, and spatial relations directly in image coordinates.
- Training signal: set-prediction detection losses.
- What it teaches: when the canvas already provides strong visual context, layout can be treated as a detection problem.
- Relevance to schematics: good when the task is really "place objects in the right regions."
- Source: https://arxiv.org/abs/2212.09877

### LACE: Towards Aligned Layout Generation via Diffusion Model

- Core idea: diffusion model for aligned layout generation, including conditional generation and refinement.
- Geometry: continuous box geometry with differentiable design constraints.
- Training signal: diffusion plus differentiable aesthetic constraints.
- What it teaches: continuous geometry makes constraints easier to encode.
- Relevance to schematics: strong choice if you want explicit validity and alignment penalties.
- Source: https://arxiv.org/abs/2402.04754

### PosterLLaVA

- Core idea: multimodal LLM framework for unified layout generation.
- Geometry: structured text / JSON-style output with visual and textual instruction tuning.
- Training signal: instruction tuning on multimodal layout data.
- What it teaches: natural-language design intent can be mapped to structured layout output.
- Relevance to schematics: good bridge between design instructions and structured geometry output.
- Source: https://arxiv.org/abs/2406.02884

### LayouSyn / Natural Scene Layout Generation with Diffusion Transformers

- Core idea: text-to-layout generation for natural scenes.
- Geometry: open-vocabulary scene objects with aspect-aware geometry.
- Training signal: conditional diffusion with a language-model scene extractor.
- What it teaches: language understanding and spatial synthesis are often better handled as separate modules.
- Relevance to schematics: useful if you want open-vocabulary composition rather than a closed symbol set.
- Source: https://arxiv.org/abs/2505.04718

### LaySPA: LLMs as Layout Designers / Enhanced Spatial Reasoning

- Core idea: train an LLM agent to improve spatial reasoning for content-aware layout generation.
- Geometry: explicit reasoning over placement, alignment, and inter-element relations.
- Training signal: RL-style rewards for geometric validity, structural fidelity, and visual quality.
- What it teaches: spatial reasoning can be learned, not assumed.
- Relevance to schematics: probably the most directly agentic paper in the set.
- Source: https://arxiv.org/abs/2509.16891

### Automatic Text Box Placement for Supporting Typographic Layout Design

- Core idea: predict where text boxes should go in an incomplete design.
- Geometry: placement completion rather than whole-layout generation.
- Training signal: supervised placement prediction on Crello.
- What it teaches: task-specific transformers can beat large general VLMs on precise placement.
- Relevance to schematics: good cautionary benchmark for exact placement problems.
- Source: https://kyushu-u.elsevierpure.com/en/publications/automatic-text-box-placement-forsupporting-typographic-design/

### Spatially-Aware Transformer for Embodied Agents

- Core idea: spatially aware transformer for episodic memory in embodied agents.
- Geometry: map-like memory with location-aware retrieval.
- Training signal: agent/memory training for place-centric tasks.
- What it teaches: spatial state can be built into memory, not just perception.
- Relevance to schematics: useful conceptually if an agent needs to remember prior placements or past layout decisions.
- Source: https://arxiv.org/abs/2402.15160

### One Transformer for Universal Room Layout Estimation

- Core idea: single-transformer room-layout estimator.
- Geometry: room boundaries, Manhattan-world structure, and geometric consistency constraints.
- Training signal: task-conditioned queries, contrastive learning, and differentiable geometric losses.
- What it teaches: geometry-first losses can remove the need for heavy post-processing.
- Relevance to schematics: a good example of baking validity into the model rather than fixing it afterward.
- Source: https://openaccess.thecvf.com/content/WACV2026/html/Mia_Layout_Anything_One_Transformer_for_Universal_Room_Layout_Estimation_WACV_2026_paper.html

---

## 4) Cross-paper synthesis

- The field splits into three useful ideas:
  - explicit geometry in the input representation,
  - better 2D positional bias inside attention,
  - and generation/refinement systems that output spatial structure directly.
- The most repeated lesson is that spatial reasoning improves when it is tied to a concrete signal:
  - bounding boxes,
  - pairwise relations,
  - patch alignment,
  - layout completion,
  - or instruction/RL supervision.
- For schematic or KiCad-style agents, the most transferable techniques are:
  - encode coordinates explicitly,
  - prefer relative relations when attachment and adjacency matter,
  - combine OCR/text and vision in one spatial frame,
  - use generation or refinement when placement is the actual task,
  - and use language instructions only after the model already has grounded geometry.

## 5) My ranked shortlist for schematic/layout agents

1. `GeoPE`
2. `Spiral RoPE`
3. `iRPE`
4. `LayoutTransformer`
5. `LayoutLMv2`
6. `LayoutLMv3`
7. `DocFormer`
8. `BLT`
9. `LayoutDM`
10. `LaySPA`

## 6) Practical stack recommendation

If I had to build a transformer for schematic geometry from this reading list, I would start with:

- `GeoPE` or `Spiral RoPE` for the coordinate system.
- `iRPE` or `Learning Spatial Decay` for local relation bias.
- `LayoutLMv2/v3` or `DocFormer` for text + geometry grounding.
- `LayoutTransformer`, `BLT`, or `LayoutDM` for placement/refinement.
- `LaySPA` if the model needs to explain or improve spatial decisions in natural language.




• The most useful ideas for electrical schematics are the ones that preserve exact
  geometry while still letting the model reason over local relationships. From the
  notes file, I’d extract these as the core design principles:

  - Attach coordinates to schematic tokens and objects from the start, not as a
    post-process. That is the main lesson from LayoutLM and the 2D understanding
    papers. See specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:28-31 and specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:307-322.
  - Prefer relative spatial relations over absolute page position. In schematics,
    “above/below/connected/adjacent/inside a block” matters more than where
    something sits on the canvas. That comes through in LayoutLMv2 and iRPE. See
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:36-39 and
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:104-108.
  - Fuse text and rendering in one spatial frame. For schematics, that means OCR
    text, symbol graphics, and wire geometry should share the same representation.
    That is the main takeaway from DocFormer, LayoutLMv3, and DiT. See specs/003-
    schematic-gym/2d-spatial-transformer-reading-notes.md:52-55, specs/003-
    schematic-gym/2d-spatial-transformer-reading-notes.md:44-47, and specs/003-
    schematic-gym/2d-spatial-transformer-reading-notes.md:68-71.
  - Bind labels to the exact pin, wire, or symbol they belong to. This is the
    local-alignment lesson from DocFormerv2. It is especially relevant for net
    labels, power symbols, and annotation-heavy schematics. See specs/003-
    schematic-gym/2d-spatial-transformer-reading-notes.md:76-80.
  - Use 2D positional encodings that respect direction and diagonals, not just x/y
    flattening. For schematic geometry, the most relevant are iRPE, 2D RoPE,
    Spiral RoPE, and GeoPE. These are the papers that most directly address false
    adjacency after flattening 2D into 1D. See specs/003-schematic-gym/2d-spatial-
    transformer-reading-notes.md:104-180.
  - Make the model robust to different canvas sizes and scales. That matters for
    schematics because sheet sizes, symbol sizes, and subcircuit densities vary a
    lot. 2D RoPE and RPE-2D are the main takeaways here. See specs/003-schematic-
    gym/2d-spatial-transformer-reading-notes.md:112-116 and specs/003-schematic-
    gym/2d-spatial-transformer-reading-notes.md:136-140.
  - Add an explicit spatial prior, not only a generic transformer stack. 2-D SSM
    is the “architectural bias” example: it bakes 2D structure into the model
    itself. That is useful if simple coordinate embeddings are too weak. See
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:152-155.
  - Treat layout as a generation and refinement problem, not just classification.
    For schematics, this is the biggest practical takeaway if you want automatic
    symbol placement or cleanup. LayoutTransformer, BLT, LayoutDM, and LACE all
    point toward iterative or probabilistic placement rather than one-shot
    prediction. See specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:60-63, specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:188-192, specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:197-201, and specs/003-schematic-gym/2d-spatial-transformer-reading-
    notes.md:242-246.
  - Use retrieval when you have recurring design patterns. In electrical
    schematics, similar blocks repeat constantly: regulators, op-amp stages,
    crystal sections, USB, power entry, etc. RALF is the clean example of using
    prior layouts as structural guidance. See specs/003-schematic-gym/2d-spatial-
    transformer-reading-notes.md:206-210.
  - Use language instructions only after the model already understands geometry.
    LayoutLLM and LaySPA are the “LLM agent” versions of this idea. They are
    useful if you want a schematic copilot that can interpret “move the decouplers
    closer” or “keep the feedback loop tight,” but they still need grounded
    spatial supervision first. See specs/003-schematic-gym/2d-spatial-transformer-
    reading-notes.md:84-87 and specs/003-schematic-gym/2d-spatial-transformer-
    reading-notes.md:269-273.
  - Use geometry-first losses and validity constraints. The big lesson from
    LayoutDiT, Automatic Text Box Placement, and One Transformer for Universal
    Room Layout Estimation is that the model should be punished for overlap,
    misalignment, or invalid geometry directly. That maps well to schematic rules
    like no label collisions, readable wire routing, and clean pin attachment. See
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:224-228,
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:278-282, and
    specs/003-schematic-gym/2d-spatial-transformer-reading-notes.md:296-300.

  What I would carry into a schematic system:

  - Represent each symbol, pin, label, and wire endpoint with explicit 2D
    coordinates.
  - Use relative-position bias or 2D RoPE-style embeddings for attention.
  - Train on rendered schematic images plus structured text/netlist data.
  - Add local-alignment objectives so labels bind to exact pins and wires.
  - Use iterative refinement or diffusion for placement, not just autoregressive
    generation.
  - Add retrieval of similar circuit blocks as a strong prior.
  - Only add LLM instruction-following after the geometry is already grounded.

