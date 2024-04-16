# -*- coding: utf-8 -*-
"""VIT.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1wBy35MiGI7cLd-njuTi0hu6ljbImKfdG

## Training Visual transformer on CIFAR-10 dataset.

The goal of this project is to understand the concept of compact vision transformers. Vision Transformers are considered to be data-hungry. Therefore, pretraining a ViT ona a large sized dataset is mostly to have equivalent performance to Convolutional Neural Network Models.

This project is based on the academic paper [Vision Transformer for Small Size Datasets]. The self attention layer of ViT lacks locality inductive bias. Images pixels are locally correlated and their correlation maps are translation invariant. ViTs needs more data to capture more information. CNNs use sliding windows to capture spatial information making them better with smaller datasets.

We will try using 2 methods to over come this problem.
- Shifted Patch Tokenization
- Locality Self Attention
"""

import numpy as np
import math
from tensorflow import keras
import matplotlib.pyplot as plt
from tensorflow.keras import layers
import tensorflow as tf

# Importing the CIFAR 10 dataset
(X_train, y_train),(X_test, y_test) = keras.datasets.cifar10.load_data()

"""I have saturated all the hyper parameters in this section. This code is similar to Keras GitHub repo. The purpose of this project is to understand the basic concepts and implementations behind research papers."""

# Setting the hyper parameters
# Data
num_classes = 10
input_shape = (32,32,3)
buffer_size = 512
batch_size = 256
# Augumentation
image_size = 72
patch_size = 6
num_patches = (image_size // patch_size) ** 2
# Optimizers
learning_rate = 0.001
weight_decay = 0.0001
# Training
epochs = 10
# Architecture
layer_norm_eps = 1e-6
transformer_layers = 8
projection_dim = 64
num_heads = 4
transformer_units = [projection_dim * 2, projection_dim]
mlp_head_units = [2048,1024]

"""To train ViTs we need to apply different data augumentation techniques such as CutMix, Mixup, Auto Augument, Repeated Augument. We will use our own augumentation technique to understand the difference."""

# Data Augumentation
data_augumentation = keras.Sequential([layers.Normalization(),
                                       layers.Resizing(image_size, image_size),
                                       layers.RandomFlip("horizontal"),
                                       layers.RandomRotation(factor = 0.02),
                                       layers.RandomZoom(height_factor = 0.2, width_factor = 0.2)], name = "data_augumentation")
data_augumentation.layers[0].adapt(X_train)

"""Method 1: Shifted Patch Tokzenization

ViT uses tokens obtained from linearly projected patches of the input images.
Shifted Patch Tokenization uses the whole image and shifts the image in diagonal directions and concats the diagonally shifted image with the original image. This creates patches which can be used generate tokens.
"""

class shiftedpatch(layers.Layer):
  def __init__(self,
               image_size = image_size,
               patch_size = patch_size,
               num_patches = num_patches,
               projection_dim = projection_dim,
               vanilla = False,
               **kwargs):
    super().__init__(**kwargs)
    self.vanilla = vanilla
    self.image_size = image_size
    self.patch_size = patch_size
    self.haff_patch = patch_size //  2
    self.flatten_patches = layers.Reshape((num_patches, -1))
    self.projection = layers.Dense(units = projection_dim)
    self.layer_norm = layers.LayerNormalization(epsilon = layer_norm_eps)

  def crop_shift_pad(self, images, mode):
    if mode == "left-up":
      crop_height = self.half_patch
      crop_width = self.half_patch
      shift_height = 0
      shift_width = 0
    elif mode == "left-down":
      crop_height = 0
      crop_width = self.half_patch
      shift_height = self.half_patch
      shift_width = 0
    elif mode == "right-up":
      crop_height = self.half_patch
      crop_width = 0
      shift_height = 0
      shift_width = self.half_patch
    else:
      crop_height = 0
      crop_width = 0
      shift_height = self.half_patch
      shift_width = self.half_patch

    crop = tf.image.crop_to_bounding_box(images,
                                         offset_height = crop_height,
                                         offset_width = crop_width,
                                         target_height = self.image_size - self.half_patch,
                                         target_width = self.image_size - self.half_patch,
                                         )
    shift_pad = tf.image.pad_to_bounding_box(crop,
                                             offset_height = shift_height,
                                             offset_width = shift_width,
                                             target_height = self.image_size,
                                             target_width = self.image_size,
                                             )
    return shift_pad

  def call(self, images):
    if not self.vanilla:
      images = tf.concat([images,
                          self.crop_shift_pad(images, mode = "left-up"),
                          self.crop_shift_pad(images, mode = "left-down"),
                          self.crop_shift_pad(images, mode = "right-up"),
                          self.crop_shift_pad(images, mode = "right-down"),
                          ], axis = -1)
    patches = tf.image.extract_patches(images = images,
                                       sizes = [1,self.patch_size, self.patch_size, 1],
                                       strides = [1, self.patch_size, self.patch_size, 1],
                                       rates = [1,1,1,1],
                                       padding = "VALID")
    flat_patches = self.flatten_patches(patches)
    if not self.vanilla:
      tokenz = self.layer_norm(flat_patches)
      tokenz = self.projection(tokenz)
    else:
      tokenz = self.projection(flat_patches)
    return (tokenz, patches)

"""Since the model does not actually know anything about the spatial relationship be- tween tokens, adding extra information to reflect that can be useful."""

class patch_encoder(layers.Layer):
  def __init__(self,
               num_patches = num_patches,
               projection_dim = projection_dim,
               **kwargs):
    super().__init__(**kwargs)
    self.num_patches = num_patches
    self.position_embedding = layers.Embedding(input_dim = num_patches, output_dim = projection_dim)
    self.positions = tf.range(start = 0, limit = self.num_patches, delta = 1)

  def call(self, encoded_patches):
    encoded_positions = self.position_embedding(self.positions)
    encoded_patches = encoded_patches + encoded_positions
    return encoded_patches

class multiheadattention(tf.keras.layers.MultiHeadAttention):
  def __init__(self, **kwargs):
    super().__init__(**kwargs)
    self.trainable = tf.Variable(math.sqrt(float(self._key_dim)), trainable = True)

  def _compute_attention(self, query, key, value, attention_mask = None, training = None):
    query = tf.multiply(query, 1.0 / self.trainable)
    attention_scores = tf.einsum(self._dot_product_equation, key, query)
    attention_scores = self._masked_softmax(attention_scores, attention_mask)
    attention_scores_drop = self._dropout_layer(attention_scores, training = training)

    attention_output = tf.einsum(self._combine_equation, attention_scores_drop, value)
    return attention_output, attention_scores

def mlp(x, hidden_units, dropout_rate):
  for units in hidden_units:
    x = layers.Dense(units, activation = tf.nn.gelu)(x)
    x = layers.Dropout(dropout_rate)(x)
  return x
diag_attn_mask = 1 - tf.eye(num_patches)
diag_attn_mask = tf.cast([diag_attn_mask], dtype = tf.int8)

def vit_classifier(vanilla = False):
  inputs = layers.Input(shape = input_shape)
  augumented = data_augumentation(inputs)
  (tokens, _) = shiftedpatch(vanilla = vanilla)(augumented)
  encoded_patches = patch_encoder()(tokens)

  for _ in range(transformer_layers):
    x1 = layers.LayerNormalization(epsilon = 1e-6)(encoded_patches)

    if not vanilla:
      attention_output = multiheadattention(num_heads = num_heads, key_dim = projection_dim, dropout = 0.1)(x1,x1, attention_mask = diag_attn_mask)
    else:
      attention_output = layers.MultiHeadAttention(num_heads= num_heads, key_dim = projection_dim, dropout = 0.1) (x1,x1)
    x2 = layers.Add()([attention_output, encoded_patches])
    x3 = layers.LayerNormalization(epsilon = 1e-6)(x2)
    x3 - mlp(x3, hidden_units = transformer_units, dropout_rate = 0.1)
    encoded_patches = layers.Add()([x3,x2])

  representation = layers.LayerNormalization(epsilon = 1e-6)(encoded_patches)
  representation = layers.Flatten()(representation)
  representation = layers.Dropout(0.5)(representation)

  features = mlp(representation, hidden_units = mlp_head_units, dropout_rate = 0.5)

  logits = layers.Dense(num_classes)(features)

  model = keras.Model(input = inputs, outputs = logits)
  return model

class warmupcosine(keras.optimizers.schedules.LearningRateSchedule):
  def __init__(self, learning_rate_base, total_steps, warmup_learning_rate, warmup_steps):
    super().__init__()
    self.learning_rate_base = learning_rate_base
    self.total_steps = total_steps
    self.warmup_learning_rate = warmup_learning_rate
    self.warmup_steps = warmup_steps
    self.pi = tf.constant(np.pi)

  def __call__(self, step):
    if self.total_steps < self.warmup_steps:
      raise ValueError("Total Steps must be larger or equal to warmup steps")

    cos_annealed_lr = tf.cos(self.pi * (tf.cast(step, tf.float32) - self.warmup_steps) / float(self.total_steps - self.warmup_steps))
    learning_rate = 0.5 * self.learning_rate_base * (1 + cos_annealed_lr)

    if self.warmup_steps > 0:
      if self.learning_rate_base < self.warmup_learning_rate:
        raise ValueError("Base learning rate must be larger than warmup learning rate")
      slope = (self.learning_rate_base - self.warmup_learning_rate) / self.warmup_steps
      warmup_rate = slope * tf.cast(step, tf.float32) + self.warmup_learning_rate
      learning_rate = tf.where(step< self.warmup_steps, warmup_rate, learning_rate)
    return tf.where(step > self.total_Steps, 0.0, learning_rate, name = "learning_rate")



def run_experiment(model):
  total_steps = int((len(X_train) / batch_size) * epochs)
  warmup_epoch_percentage = 0.10
  warmup_steps = int(total_steps * warmup_epoch_percentage)
  scheduled_lrs = warmupcosine(learning_rate_base = learning_rate,
                               total_steps = total_steps,
                               warmup_learning_rate = 0.0,
                               warmup_steps = warmup_steps)
  optimizer = tf.optimizers.AdamW(learning_rate = learning_rate, weight_decay = weight_decay)

  model.compile(optimizer = optimizer,
                loss = keras.losses.SparseCategoricalCrossengtropy(from_logits = True),
                metrics = [keras.metrics.SparseCategoricalAccuracy(name = "accuracy"),
                           keras.metrics.SparseTopKCategoricalAccuracy(5, name = "top-5-accuracy")])

  history = model.fit(x = X_train,
                      y = y_train,
                      batch_size = batch_size,
                      epochs = epochs,
                      validation_split = 0.1)
  _, accuracy, top_5_accuracy = model.evaluate(X_test, y_test, batch_size = batch_size)
  print(f"Test Accuracy : {round(accuracy * 100, 2)}%")
  print(f"Test Top 5 Accuracy : {round(top_5_accuracy * 100, 2)}%")
  return history

vit = vit_classifier(vanilla = True)
history = run_experiment(vit)
