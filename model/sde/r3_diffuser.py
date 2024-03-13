"""R^3 diffusion methods."""
import numpy as np
from scipy.special import gamma

import torch

class R3Diffuser:
    """VP-SDE diffuser class for translations."""

    def __init__(self, dim=3, min_b=0.1, max_b=20.0, scaling=0.1):
        """
        Args:
            min_b: starting value in variance schedule.
            max_b: ending value in variance schedule.
        """
        self.dim = dim
        self.min_b = min_b
        self.max_b = max_b
        self.scaling = scaling

    def _scale(self, x):
        return x * self.scaling

    def _unscale(self, x):
        return x / self.scaling

    def b_t(self, t):
        if np.any(t < 0) or np.any(t > 1):
            raise ValueError(f'Invalid t={t}')
        return self.min_b + t*(self.max_b - self.min_b)

    def diffusion_coef(self, t):
        """Time-dependent diffusion coefficient."""
        return np.sqrt(self.b_t(t))

    def drift_coef(self, x, t):
        """Time-dependent drift coefficient."""
        return -1/2 * self.b_t(t) * x

    def sample_ref(self, n_samples: float=1):
        return np.random.normal(size=(n_samples, self.dim))

    def marginal_b_t(self, t):
        return t*self.min_b + (1/2)*(t**2)*(self.max_b-self.min_b)

    def calc_trans_0(self, score_t, x_t, t, use_torch=True):
        beta_t = self.marginal_b_t(t)
        beta_t = beta_t[..., None, None]
        exp_fn = torch.exp if use_torch else np.exp
        cond_var = 1 - exp_fn(-beta_t)
        return (score_t * cond_var + x_t) / exp_fn(-1/2*beta_t)

    def forward(self, x_t_1: np.ndarray, t: float, num_t: int):
        """Samples marginal p(x(t) | x(t-1)).

        Args:
            x_0: [..., n, 3] initial positions in Angstroms.
            t: continuous time in [0, 1].

        Returns:
            x_t: [..., n, 3] positions at time t in Angstroms.
            score_t: [..., n, 3] score at time t in scaled Angstroms.
        """
        if not np.isscalar(t):
            raise ValueError(f'{t} must be a scalar.')
        x_t_1 = self._scale(x_t_1)
        b_t = torch.tensor(self.marginal_b_t(t) / num_t).to(x_t_1.device)
        z_t_1 = torch.tensor(np.random.normal(size=x_t_1.shape)).to(x_t_1.device)
        x_t = torch.sqrt(1 - b_t) * x_t_1 + torch.sqrt(b_t) * z_t_1
        return x_t

    def distribution(self, x_t, score_t, t, mask, dt):
        x_t = self._scale(x_t)
        g_t = self.diffusion_coef(t)
        f_t = self.drift_coef(x_t, t)
        std = g_t * np.sqrt(dt)
        mu = x_t - (f_t - g_t**2 * score_t) * dt
        if mask is not None:
            mu *= mask[..., None]
        return mu, std

    def forward_marginal(self, x_0, t):
        """Samples marginal p(x(t) | x(0)).

        Args:
            x_0: [..., n, d] initial positions in Angstroms.
            t: continuous time in [0, 1].

        Returns:
            x_t: [..., n, d] positions at time t in Angstroms.
            score_t: [..., n, d] score at time t in scaled Angstroms.
        """

        x_0 = self._scale(x_0)

        log_mean_coeff = -0.5 * self.marginal_b_t(t)

        cast_shape = [log_mean_coeff.shape[0]] + [1] * (x_0.ndim - 1)
        log_mean_coeff = torch.reshape(log_mean_coeff, cast_shape)

        mean = torch.exp(log_mean_coeff) * x_0
        std = torch.sqrt(1. - torch.exp(2. * log_mean_coeff))

        x_t = torch.normal(mean = mean, std = std)

        score_t = self.score(x_t, x_0, t)

        x_t = self._unscale(x_t)

        return x_t, score_t

    def score_scaling(self, t):
        return 1 / torch.sqrt(self.conditional_var(t))

    def reverse(
            self,
            *,
            x_t: np.ndarray,
            score_t: np.ndarray,
            t: float,
            dt: float,
            mask: np.ndarray=None,
            center: bool=True,
            noise_scale: float=1.0,
        ):
        """Simulates the reverse SDE for 1 step

        Args:
            x_t: [..., 3] current positions at time t in angstroms.
            score_t: [..., 3] rotation score at time t.
            t: continuous time in [0, 1].
            dt: continuous step size in [0, 1].
            mask: True indicates which residues to diffuse.

        Returns:
            [..., 3] positions at next step t-1.
        """
        if not np.isscalar(t):
            raise ValueError(f'{t} must be a scalar.')
        x_t = self._scale(x_t)
        g_t = self.diffusion_coef(t)
        f_t = self.drift_coef(x_t, t)
        z = noise_scale * np.random.normal(size=score_t.shape)
        perturb = (f_t - g_t**2 * score_t) * dt + g_t * np.sqrt(dt) * z

        if mask is not None:
            perturb *= mask[..., None]
        else:
            mask = np.ones(x_t.shape[:-1])
        x_t_1 = x_t - perturb
        if center:
            com = np.sum(x_t_1, axis=-2) / np.sum(mask, axis=-1)[..., None]
            x_t_1 -= com[..., None, :]
        x_t_1 = self._unscale(x_t_1)
        return x_t_1

    def conditional_var(self, t, use_torch=True):
        """Conditional variance of p(xt|x0).

        Var[x_t|x_0] = conditional_var(t)*I

        """
        return 1 - torch.exp(-self.marginal_b_t(t))

    def score(self, x_t, x_0, t, scale=False):
        if scale:
            x_t = self._scale(x_t)
            x_0 = self._scale(x_0)
        t = t[:,None,None]

        return -(x_t - torch.exp(-1/2*self.marginal_b_t(t)) * x_0) / self.conditional_var(t)

if __name__=='__main__':
    import pdb
    x_0_tri = torch.tensor([[0, 0], [0, 1], [1, 0]])
    x_0_tan = torch.tensor([[0, 0], [0, 1], [1, 0], [1, 1]])

    diffuser_3d = R3Diffuser(dim=3)
    diffuser_4d = R3Diffuser(dim=4)

    t = torch.tensor([0.5])

    x_t_tri, score_tri = diffuser_3d.forward_marginal(x_0_tri, t)
    x_t_tan, score_tan = diffuser_4d.forward_marginal(x_0_tan, t)
    pdb.set_trace()

