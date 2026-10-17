import torch
import torch.nn as nn


class DelayNet(nn.Module):
    def __init__(self, vocab_sizes: dict, num_numeric: int, emb_dim: int = 8):
        super().__init__()

        # One embedding table per categorical column
        self.embeddings = nn.ModuleList([
            nn.Embedding(size, emb_dim) for size in vocab_sizes.values()
        ])

        total_emb = emb_dim * len(vocab_sizes)
        input_dim = total_emb + num_numeric

        # MLP Here
        self.mlp = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),  # Single output here
        )

    # Pushing data through the MLP
    def forward(self, cat, num):
        # cat: (batch, n_cat_cols) long  |  num: (batch, n_numeric) float
        embs = [emb(cat[:, i]) for i, emb in enumerate(self.embeddings)]
        x = torch.cat(embs + [num], dim=1)
        return self.mlp(x)