from typing import Dict, List, Optional
from typing_extensions import Literal

import numpy as np
import torch
from torch import Tensor
import torch.nn as nn
import torch.nn.functional as F
import torchvision

__all__ = [
    "GeneralizedCELoss",
    "GradReverse",
    "MixedLoss",
    "PixelCrossEntropy",
    "VGGLoss",
    "contrastive_gradient_penalty",
    "grad_reverse",
]


class GradReverse(torch.autograd.Function):
    """Gradient reversal layer"""

    @staticmethod
    def forward(ctx, x, lambda_):
        ctx.lambda_ = lambda_
        return x.view_as(x)

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output.neg().mul(ctx.lambda_), None


def grad_reverse(features, lambda_=1.0):
    return GradReverse.apply(features, lambda_)


def contrastive_gradient_penalty(network, input, penalty_amount=1.0):
    """Contrastive gradient penalty.

    This is essentially the optimization introduced by Mescheder et al 2018.

    Args:
        network: Network to apply penalty through.
        input: Input or list of inputs for network.
        penalty_amount: Amount of penalty.

    Returns:
        torch.Tensor: gradient penalty optimization.

    """

    def _get_gradient(inp, output):
        gradient = torch.autograd.grad(
            outputs=output,
            inputs=inp,
            grad_outputs=torch.ones_like(output),
            create_graph=True,
            retain_graph=True,
            only_inputs=True,
            allow_unused=True,
        )[0]
        return gradient

    if not isinstance(input, (list, tuple)):
        input = [input]

    input = [inp.detach() for inp in input]
    input = [inp.requires_grad_() for inp in input]

    with torch.set_grad_enabled(True):
        output = network(*input)[-1]
    gradient = _get_gradient(input, output)
    gradient = gradient.view(gradient.size()[0], -1)
    penalty = (gradient ** 2).sum(1).mean()

    return penalty * penalty_amount


class PixelCrossEntropy(nn.CrossEntropyLoss):
    def __init__(
        self, weight=None, size_average=None, ignore_index=-100, reduce=None, reduction="mean"
    ):
        super().__init__(weight, size_average, ignore_index, reduce, reduction)

    def forward(self, input: Tensor, target: Tensor):
        input = input.view(input.size(0), 256, *target.shape[1:])
        # make integer class labels
        target = (target * (256 - 1)).long()
        return super().forward(input, target)


class VGGLoss(nn.Module):
    """
    The VGG loss based on the ReLU activation layers of the pre-trained 19 layer VGG network. This
    is calculated as the euclidean distance between the feature representations of a reconstructed
    image.
    """

    feature_layer_default: int = 22  # VGG19 layer number from which to extract features

    def __init__(self, feature_layer: Optional[int] = None, prefactor=0.006):
        """
        Args:
            prefactor: prefactor by which to scale the loss.
                       Rescaling by a factor of 1 / 12.75 gives VGG losses of a scale that
                       is comparable to MSE loss. This is equivalent to multiplying with a
                       rescaling factor of ??? 0.006.
        """
        super().__init__()
        vgg_features = torchvision.models.vgg19(contexted=True).features
        modules = [m for m in vgg_features]

        vgg_feature_layer = self.feature_layer_default if feature_layer is None else feature_layer
        if vgg_feature_layer == 22:
            self.vgg = nn.Sequential(*modules[:8])
        elif vgg_feature_layer == 54:
            self.vgg = nn.Sequential(*modules[:35])
        else:
            raise ValueError("'vgg_feature_layer' has to be either 22 or 54")

        self.vgg.requires_grad = False
        self.prefactor = prefactor

    def _extract_feature(self, x):
        return self.vgg(x)

    def forward(self, noisy, clean):
        vgg_noisy = self._extract_feature(noisy)
        with torch.no_grad():
            vgg_clean = self._extract_feature(clean.detach())

        return self.prefactor * F.mse_loss(vgg_noisy, vgg_clean)


class MixedLoss(nn.Module):
    """Mix of cross entropy and MSE"""

    def __init__(
        self,
        feature_group_slices: Dict[str, List[slice]],
        disc_loss_factor: float = 1.0,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        super().__init__()
        assert feature_group_slices["discrete"][0].start == 0, "x has to start with disc features"
        self.disc_feature_slices = feature_group_slices["discrete"]
        assert all(group.stop - group.start >= 2 for group in self.disc_feature_slices)
        self.cont_start = self.disc_feature_slices[-1].stop
        self.disc_loss_factor = disc_loss_factor
        self.reduction = reduction

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        disc_loss = input.new_zeros(())
        # for the discrete features do cross entropy loss
        for disc_slice in self.disc_feature_slices:
            disc_loss += F.cross_entropy(
                input[:, disc_slice], target[:, disc_slice].argmax(dim=1), reduction=self.reduction
            )
        # for the continuous features do MSE
        cont_loss = F.mse_loss(
            input[:, self.cont_start :], target[:, self.cont_start :], reduction=self.reduction
        )
        return self.disc_loss_factor * disc_loss + cont_loss


class GeneralizedCELoss(nn.Module):
    def __init__(self, q: float = 0.7):
        super(GeneralizedCELoss, self).__init__()
        self.q = q

    def forward(self, logits: Tensor, targets: Tensor) -> Tensor:
        p = F.softmax(logits, dim=1)
        if np.isnan(p.mean().item()):
            raise NameError("GCE_p")
        Yg = torch.gather(p, 1, torch.unsqueeze(targets, 1))
        # modify gradient of cross entropy
        loss_weight = (Yg.squeeze().detach() ** self.q) * self.q
        return F.cross_entropy(logits, targets, reduction="none") * loss_weight
