from dataclasses import dataclass, field


@dataclass(slots=True)
class ModelConfig:
    graph_hidden: int = 384
    qwen_hidden: int = 1024
    max_graph_tokens: int = 128


@dataclass(slots=True)
class TrainingConfig:
    batch_size: int = 8
    learning_rate: float = 1e-4
    weight_decay: float = 0.01
    max_epochs: int = 10
    grad_clip_norm: float = 1.0
    seed: int = 42


@dataclass(slots=True)
class DataConfig:
    train_path: str = "data/train.jsonl"
    val_path: str = "data/val.jsonl"
    test_path: str = "data/test.jsonl"
    num_workers: int = 4
    shuffle: bool = True


@dataclass(slots=True)
class ProjectConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    experiment_name: str = "baseline"
