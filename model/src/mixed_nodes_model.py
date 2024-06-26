# ******************************************************************************
#  File:            mixde_nodes_model.py
#  Master's Thesis: Evaluating Reliability of Static Analysis Results
#                   Using Machine Learning
#  Author:          Beranek Tomas (xberan46)
#  Date:            14.5.2024
#  Up2date sources: https://github.com/TomasBeranek/but-masters-thesis
#  Description:     Script for model training.
# ******************************************************************************

import tensorflow as tf
import tensorflow_gnn as tfgnn
import sys
from tensorflow_gnn.models import mt_albis
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras import backend as K
import json
import os
import re


# Hyperparameters (values not defined here have default values)
hyperparameters = {
  'epochs': 200,
  'learning_rate': 0.0001,
  'early_stopping_patience': 15,
  'batch_size': 6,
  'num_graph_updates': 9,
  'node_state_dim': 18,
  'receiver_tag': tfgnn.TARGET, # tfgnn.TARGET (along edge direction) or tfgnn.SOURCE (against edge direction)
  'message_dim': 18, # set to the same value as 'node_state_dim'
  'state_dropout_rate': 0.2,
  'edge_dropout_rate': 0, # 0 (to emulate VanillaMPNN) or same as 'state_dropout_rate'
  'l2_regularization': 1e-5, # e.g. 1e-5
  'attention_type': 'none', # "none", "multi_head", or "gat_v2",
  'attention_num_heads': 4, # 4 is default
  'simple_conv_reduce_type': 'mean|sum', # 'mean', 'mean|sum', ...
  'normalization_type': 'layer', # 'layer', 'batch', or 'none'
  'next_state_type': 'residual', # 'residual' or 'dense' - Input layer must have same size of HIDDEN_STATE as units for 'residual'
  'note': 'Model 8' # description of changes since the last version
}


# Define preprocess model which will ONLY extract labels out of graph
def preprocess(preproc_input_spec, ds_batched):
  preproc_input = tf.keras.layers.Input(type_spec=preproc_input_spec)
  graph = preproc_input.merge_batch_to_components()
  labels = tfgnn.keras.layers.Readout(from_context=True, feature_name='label')(graph)
  graph = graph.remove_features(context=["label"])
  assert "label" not in graph.context.features
  preproc_model = tf.keras.Model(preproc_input, (graph, labels))
  ds_batched = ds_batched.map(preproc_model)
  model_input_spec, _ = ds_batched.element_spec # We dont need spec for labels
  return model_input_spec, ds_batched


# Encode input nodeset features into a single tensor (+ add some trainable parameters to the transformation)
def set_initial_node_state(node_set, *, node_set_name):
  features = node_set.features # Immutable view

  # Combine all features to a single vector (since all of them are float32 scalars)
  stacked_features = tf.stack([v for _, v in sorted(features.items())], axis=1)
  return tf.keras.layers.Dense(hyperparameters['node_state_dim'], 'relu')(stacked_features)


def encode_ARGUMENT_INDEX(edge_set, *, edge_set_name):
  if edge_set_name != 'ARGUMENT':
    return {'ARGUMENT_INDEX': tfgnn.keras.layers.MakeEmptyFeature()(edge_set)}

  stacked_features = tf.stack([edge_set.features['ARGUMENT_INDEX']], axis=1)
  return {'ARGUMENT_INDEX': tf.keras.layers.Dense(hyperparameters['node_state_dim'], 'relu')(stacked_features)}


def drop_all_features(_, **unused_kwargs):
  return {}


def build_model(model_input_spec):
  # Input layer (takes input graphs with defined TFGNN schema)
  graph = inputs = tf.keras.layers.Input(type_spec=model_input_spec)

  # Set initial states
  graph = tfgnn.keras.layers.MapFeatures(
      node_sets_fn=set_initial_node_state,
      edge_sets_fn=drop_all_features)(graph)

  # Layers of updates
  for i in range(hyperparameters['num_graph_updates']):
    graph = mt_albis.MtAlbisGraphUpdate(
        units=hyperparameters['node_state_dim'],
        message_dim=hyperparameters['message_dim'],
        receiver_tag=hyperparameters['receiver_tag'],
        node_set_names=None if i < hyperparameters['num_graph_updates']-1 else ["AST_NODE"],
        state_dropout_rate=hyperparameters['state_dropout_rate'],
        edge_dropout_rate=hyperparameters['edge_dropout_rate'],
        l2_regularization=hyperparameters['l2_regularization'],
        attention_type=hyperparameters['attention_type'],
        attention_num_heads=hyperparameters['attention_num_heads'],
        simple_conv_reduce_type=hyperparameters['simple_conv_reduce_type'],
        normalization_type=hyperparameters['normalization_type'],
        next_state_type=hyperparameters['next_state_type']
    )(graph)

  # Read hidden states from AST_NODE nodeset
  node_features = tfgnn.keras.layers.Pool(tfgnn.CONTEXT, "max", node_set_name=['AST_NODE'])(graph)

  # Extract BUG_TYPE context feature
  bug_type_feature = tfgnn.keras.layers.Readout(from_context=True, feature_name="BUG_TYPE")(graph)

  # Extract LINE context feature
  line_feature = tfgnn.keras.layers.Readout(from_context=True, feature_name="LINE")(graph)

  # Expand dimensions of context features
  bug_type_feature = tf.expand_dims(bug_type_feature, -1)
  line_feature = tf.expand_dims(line_feature, -1)

  # Concatenate context features
  context_features = tf.keras.layers.Concatenate()([bug_type_feature, line_feature])

  # Add more neurons to the head
  context_dense = tf.keras.layers.Dense(4, activation='relu')(context_features)
  nodes_dense = tf.keras.layers.Dense(8, activation='relu')(node_features)
  combined_features = tf.keras.layers.Concatenate()([context_dense, nodes_dense])

  # Add 'head' - final part of GNN which outputs a single number
  y = tf.keras.layers.Dense(1, activation='sigmoid')(combined_features)
  return tf.keras.Model(inputs, y)


def save_model(models_dir, results, combined):
  max_id = 0

  # Find latest (max) id
  for entry in os.listdir(models_dir):
    relative_path = os.path.join(models_dir, entry)
    if os.path.isdir(relative_path) and bool(re.match(r'^\d+_', entry)):
      id = int(entry.split('_')[0])
      max_id = max(max_id, id)

  new_id = max_id + 1

  # We use average val_auc of the models as a measure of architecture/hyperparameters quality
  if combined:
    avg_val_auc = results['combined'][1]
  else:
    avg_val_auc = (results['libtiff'][1] + results['httpd'][1] + results['nginx'][1]) / 3
  formatted_avg_val_auc = format(avg_val_auc, ".3f")

  # Make parent dir for all the models
  models_parent_dir_name = f'{models_dir}/{new_id}_AUC_{formatted_avg_val_auc}'
  os.makedirs(models_parent_dir_name)

  if combined:
    model = results['combined'][0]
    model.save(f'{models_parent_dir_name}/combined_AUC_{formatted_avg_val_auc}')
  else:
    # Save all the individual models
    for project, (model, max_val_auc) in results.items():
      formatted_max_val_auc = format(max_val_auc, ".3f")
      model.save(f'{models_parent_dir_name}/{project}_AUC_{formatted_max_val_auc}')

  # Save hyperparameters as JSON file
  with open(f'{models_parent_dir_name}/hyperparameters.json', 'w') as f:
    json.dump(hyperparameters, f, indent=4)


def prepare_data(schema_path, project_path, project, dataset_len):
  # Load schema
  graph_schema = tfgnn.read_schema(schema_path)
  graph_tensor_spec = tfgnn.create_graph_spec_from_schema_pb(graph_schema)

  train_positive_samples = dataset_len[project][1]
  train_negative_samples = dataset_len[project][0]

  # Read the dataset
  if project == 'libtiff+httpd+nginx':
    train_ds_files_1 = [f'{project_path}/libtiff_1.tfrecords.train',
                        f'{project_path}/httpd_1.tfrecords.train',
                        f'{project_path}/nginx_1.tfrecords.train']
    train_ds_files_0 = [f'{project_path}/libtiff_0.tfrecords.train',
                        f'{project_path}/httpd_0.tfrecords.train',
                        f'{project_path}/nginx_0.tfrecords.train']
    val_ds_files = [f'{project_path}/libtiff_0.tfrecords.val',
                    f'{project_path}/httpd_0.tfrecords.val',
                    f'{project_path}/nginx_0.tfrecords.val',
                    f'{project_path}/libtiff_1.tfrecords.val',
                    f'{project_path}/httpd_1.tfrecords.val',
                    f'{project_path}/nginx_1.tfrecords.val']
    train_ds_1 = tf.data.TFRecordDataset(train_ds_files_1, num_parallel_reads=3)
    train_ds_0 = tf.data.TFRecordDataset(train_ds_files_0, num_parallel_reads=3)
    val_ds = tf.data.TFRecordDataset(val_ds_files, num_parallel_reads=6)
  else:
    train_ds_1 = tf.data.TFRecordDataset([f'{project_path}/{project}_1.tfrecords.train'])
    train_ds_0 = tf.data.TFRecordDataset([f'{project_path}/{project}_0.tfrecords.train'])
    val_ds = tf.data.TFRecordDataset([f'{project_path}/{project}_1.tfrecords.val', f'{project_path}/{project}_0.tfrecords.val'])

  # Up-sample
  up_sample_coeff = train_negative_samples // train_positive_samples
  train_ds_1 = train_ds_1.repeat(up_sample_coeff)

  # Interleave positive and negative samples
  train_ds = tf.data.Dataset.sample_from_datasets([train_ds_0, train_ds_1], stop_on_empty_dataset=True)

  # Shuffle training samples
  buffer_size = 10_000
  train_ds = train_ds.shuffle(buffer_size)
  train_ds = train_ds.prefetch(buffer_size=tf.data.AUTOTUNE)
  val_ds = val_ds.prefetch(buffer_size=tf.data.AUTOTUNE)

  # Batch the datasets
  train_ds_batched = train_ds.batch(batch_size=hyperparameters['batch_size']).repeat()
  val_ds_batched = val_ds.batch(batch_size=hyperparameters['batch_size'])

  # Parse tf.Example protos
  train_ds_batched = train_ds_batched.map(tfgnn.keras.layers.ParseExample(graph_tensor_spec))
  val_ds_batched = val_ds_batched.map(tfgnn.keras.layers.ParseExample(graph_tensor_spec))
  preproc_input_spec_train = train_ds_batched.element_spec
  preproc_input_spec_val = val_ds_batched.element_spec

  # Apply preprocess model
  model_input_spec_train, train_ds_batched = preprocess(preproc_input_spec_train, train_ds_batched)
  model_input_spec_val, val_ds_batched = preprocess(preproc_input_spec_val, val_ds_batched)

  return model_input_spec_train, train_ds_batched, model_input_spec_val, val_ds_batched


def train_model(model_input_spec_train, train_ds_batched, val_ds_batched, train_ds_len):
  # Define Loss and Metrics
  # with tf.device('/CPU:0'):  # Forces the operations to be executed on the CPU
  model = build_model(model_input_spec_train)

  loss = tf.keras.losses.BinaryCrossentropy()
  metrics = [ tf.keras.metrics.Precision(),
              tf.keras.metrics.Recall(),
              tf.keras.metrics.AUC(),
              tf.keras.metrics.AUC(curve='PR', name='auc_pr')]

  steps_per_epoch = train_ds_len // hyperparameters['batch_size']

  optimizer = tf.keras.optimizers.Adam(learning_rate=hyperparameters['learning_rate'])

  # Compile the keras model
  model.compile(optimizer, loss=loss, metrics=metrics)
  model.summary()

  early_stopping = EarlyStopping(monitor='val_auc',
                                min_delta=0.0001,
                                patience=hyperparameters['early_stopping_patience'],
                                restore_best_weights=True)

  # Train the model
  history = model.fit(train_ds_batched,
                      steps_per_epoch=steps_per_epoch,
                      epochs=hyperparameters['epochs'],
                      validation_data=val_ds_batched,
                      shuffle=True,
                      callbacks=[early_stopping])
  return model, history


if __name__ == '__main__':
  # Load args
  schema_path = sys.argv[1] # schemas/mixed_nodes/extended_cpg.pbtxt
  project_path = sys.argv[2] # ../dataset/graph-d2a
  models_dir = sys.argv[3] # saved_models
  combined = (len(sys.argv) > 4 and sys.argv[4] == 'combined')

  dataset_len = {
    'libtiff': [7325, 371],
    'httpd': [7502, 149],
    'nginx': [13391, 319],
    'libtiff+httpd+nginx': [7325 + 7502 + 13391, 371 + 149 + 319],
  }

  results = {}

  if combined:
    project = 'libtiff+httpd+nginx'

    # Load, preprocess, balance and batch dataset
    model_input_spec_train, train_ds_batched, _, val_ds_batched = prepare_data(schema_path, project_path, project, dataset_len)

    # Train the model for the current project
    train_ds_len = sum(dataset_len[project])
    model, history = train_model(model_input_spec_train, train_ds_batched, val_ds_batched, train_ds_len)
    max_val_auc = max(history.history['val_auc'])
    results['combined'] = (model, max_val_auc)

    # Save combined model
    save_model(models_dir, results, combined=True)
  else:
    # We do a form of k-validation - the same model architecture is tested on the following projects
    for project in ['libtiff', 'httpd', 'nginx']:
      # Load, preprocess, balance and batch dataset
      model_input_spec_train, train_ds_batched, _, val_ds_batched = prepare_data(schema_path, project_path, project, dataset_len)

      # Train the model for the current project
      train_ds_len = sum(dataset_len[project])
      model, history = train_model(model_input_spec_train, train_ds_batched, val_ds_batched, train_ds_len)

      max_val_auc = max(history.history['val_auc'])
      results[project] = (model, max_val_auc)

      # To prevent TF/Keras from behaving like all the models are the same one
      K.clear_session()

    # Save models and it's hyperparameters (same hyperparameters for all saved models)
    save_model(models_dir, results, combined=False)
