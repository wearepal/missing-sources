#!/bin/bash

data=cmnist/2dig
enc=mnist
clust=vague_spaceship_improved
adapt=cmnist/fallen_sun

# ranking
python run_both.py \
    data=$data \
    enc=$enc \
    clust=$clust \
    adapt=$adapt \
    clust.method=pl_enc_no_norm \
    clust.pseudo_labeler=ranking \
    misc.log_method=ranking-fdm \
    "$@"

# kmeans
python run_both.py \
    data=$data \
    enc=$enc \
    clust=$clust \
    adapt=$adapt \
    clust.method=kmeans \
    misc.log_method=kmeans-fdm \
    "$@"

# no cluster
python run_dis.py \
    data=$data \
    enc=$enc \
    adapt=$adapt \
    adapt.balanced_context=false \
    misc.log_method=no-cluster-fdm \
    "$@"

# perfect cluster
python run_dis.py \
    data=$data \
    enc=$enc \
    adapt=$adapt \
    adapt.balanced_context=true \
    misc.log_method=perfect-cluster \
    "$@"
