import torch

from src.models.autoencoder import BlockAutoencoder
from src.models.cnn1d import CNN1DFusion
from src.models.common_torch import train_binary_model
from src.models.mlp import MLPClassifier


def test_neural_model_shapes_keep_block_and_macro_branches_separate():
    macro = torch.randn(4, 507)
    blocks = torch.randn(4, 2000)
    mlp = MLPClassifier(507)
    ae = BlockAutoencoder(input_dim=2000, latent_dim=32)
    cnn = CNN1DFusion(macro_dim=507)
    assert mlp(macro).shape == (4,)
    reconstruction, latent = ae(blocks)
    assert reconstruction.shape == (4, 2000)
    assert latent.shape == (4, 32)
    assert cnn(macro, blocks).shape == (4,)


def test_batchnorm_training_handles_a_final_singleton_batch():
    model = MLPClassifier(4)
    train_x = torch.randn(9, 4)
    train_y = torch.tensor([0, 1, 0, 1, 0, 1, 0, 1, 0])
    valid_x = torch.randn(4, 4)
    valid_y = torch.tensor([0, 1, 0, 1])
    result = train_binary_model(
        model,
        (train_x,),
        train_y,
        (valid_x,),
        valid_y,
        device=torch.device("cpu"),
        pos_weight=1.0,
        batch_size=8,
        max_epochs=1,
    )
    assert result.best_epoch == 0
