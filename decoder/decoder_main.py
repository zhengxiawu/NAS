from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import argparse
import numpy as np
import scipy.stats
import tensorflow as tf
import decoder
import six
import json
import collections
from tensorflow.python.ops import lookup_ops

_NUM_SAMPLES = {
  'train' : 500,
  'test' : 100,
}


# Basic model parameters.

parser = argparse.ArgumentParser()

parser.add_argument('--mode', type=str, default='train')
parser.add_argument('--data_dir', type=str, default='data')
parser.add_argument('--model_dir', type=str, default='model')
parser.add_argument('--restore', action='store_true', default=False)
parser.add_argument('--hidden_size', type=int, default=32)
parser.add_argument('--B', type=int, default=5)
parser.add_argument('--weight_decay', type=float, default=1e-4)
parser.add_argument('--vocab_size', type=float, default=26)
parser.add_argument('--train_epochs', type=int, default=1000)
parser.add_argument('--eval_frequency', type=int, default=10)
parser.add_argument('--batch_size', type=int, default=128)
parser.add_argument('--lr', type=float, default=1.0)
parser.add_argument('--optimizer', type=str, default='adam')
parser.add_argument('--start_decay_step', type=int, default=100)
parser.add_argument('--decay_steps', type=int, default=1000)
parser.add_argument('--decay_factor', type=float, default=0.9)
parser.add_argument('--max_gradient_norm', type=float, default=5.0)
parser.add_argument('--time_major', action='store_true', default=False)
parser.add_argument('--predict_from_file', type=str, default=None)
parser.add_argument('--predict_to_file', type=str, default=None)

SOS=0
EOS=0

def input_fn(params, mode, data_dir, batch_size, num_epochs=1):
  """Input_fn using the tf.data input pipeline for CIFAR-10 dataset.

  Args:
    is_training: A boolean denoting whether the input is for training.
    data_dir: The directory containing the input data.
    batch_size: The number of samples per batch.
    num_epochs: The number of epochs to repeat the dataset.

  Returns:
    A tuple of images and labels.
  """
  def get_filenames(mode, data_dir):
    """Returns a list of filenames."""
    if mode == 'train':
      return [os.path.join(data_dir, 'train.input'), os.path.join(data_dir, 'train.target')]
    else:
      return [os.path.join(data_dir, 'test.input'), os.path.join(data_dir, 'test.target')]

  files = get_filenames(mode, data_dir)
  input_dataset = tf.data.TextLineDataset(files[0])
  target_dataset = tf.data.TextLineDataset(files[1])
  
  dataset = tf.data.Dataset.zip((input_dataset, target_dataset))

  is_training = mode == 'train'

  if is_training:
    dataset = dataset.shuffle(buffer_size=_NUM_SAMPLES['train'])

  def decode_record(src, tgt):
    sos_id = tf.constant([SOS])
    eos_id = tf.constant([EOS])
    src = tf.string_split([src]).values
    src = tf.string_to_number(src, out_type=tf.float32)[:params['hidden_size']]
    tgt = tf.string_split([tgt]).values
    tgt = tf.string_to_number(tgt, out_type=tf.int32)[:params['length']]
    tgt_input = tf.concat([sos_id ,tgt[:-1]], axis=0)
    return (src, tgt_input, tgt)

  dataset = dataset.map(decode_record)
  dataset = dataset.repeat(num_epochs)
  dataset = dataset.batch(batch_size)
  iterator = dataset.make_one_shot_iterator()
  batched_examples = iterator.get_next()

  inputs, targets_inputs, targets = batched_examples

  assert inputs.shape.ndims == 2
  assert targets_inputs.shape.ndims == 2
  assert targets.shape.ndims == 2
  
  return {
    "inputs" : inputs,
    "targets_inputs" : targets_inputs,
    "targets" : targets}, targets

def create_vocab_tables(vocab_file):
  """Creates vocab tables for src_vocab_file and tgt_vocab_file."""
  vocab_table = lookup_ops.index_table_from_file(
      vocab_file, default_value=0)
  return vocab_table

def predict_from_file(estimator, batch_size, predict_from_file, predict_to_file):
  def infer_input_fn():
    sos_id = tf.constant([SOS])
    dataset = tf.data.TextLineDataset(predict_from_file)
    dataset = dataset.map(lambda record: tf.string_to_number(record, out_type=tf.float32), sos_id)
    dataset = dataset.batch(FLAGS.batch_size)
    iterator = dataset.make_one_shot_iterator()
    inputs, targets_inputs = iterator.get_next()
    assert inputs.shape.ndims == 2
    assert targets_inputs.shape.ndims == 2
    
    return {
      'inputs' : inputs, 
      'targets_inputs' : targets_inputs,
      'targets' : None,
    }, None

  results = []
  result_iter = estimator.predict(infer_input_fn)
  for result in result_iter:
    output = result['output'].flatten()
    output = ' '.join(map(str, output))
    tf.logging.info('Inference results OUTPUT: %s' % output)
    results.append(output)

  if decode_to_file:
    output_filename = decode_to_file
  else:
    output_filename = '%s.result' % predict_from_file
    
  tf.logging.info('Writing results into {0}'.format(output_filename))
  with tf.gfile.Open(output_filename, 'w') as f:
    for res in results:
      f.write('%s\n' % (res))

def _del_dict_nones(d):
  for k in list(d.keys()):
    if d[k] is None:
      del d[k]


def model_fn(features, labels, mode, params):
  if mode == tf.estimator.ModeKeys.TRAIN:
    inputs = features['inputs']
    targets_inputs = features['targets_inputs']
    targets = labels
    model = decoder.Model(inputs, targets_inputs, targets, params, mode)
    res = model.train()
    train_op = res['train_op']
    loss = res['loss']
    return tf.estimator.EstimatorSpec(
      mode=mode,
      loss=loss,
      train_op=train_op)
  elif mode == tf.estimator.ModeKeys.EVAL:
    inputs = features['inputs']
    targets_inputs = features['targets_inputs']
    targets = labels
    model = decoder.Model(inputs, targets_inputs, targets, params, mode)
    res = model.eval()
    loss = res['loss']
    return tf.estimator.EstimatorSpec(
      mode=mode,
      loss=loss)
  elif mode == tf.estimator.Model.predict:
    inputs = features['inputs']
    targets_inputs = features['targets_inputs']
    targets = features['targets']
    model = decoder.Model(inputs, targets_inputs, targets, params, mode)
    res = model.decode()
    sample_id = res['sample_id']
    predictions = {
      'inputs' : inputs,
      'targets' : targets,
      'output' : sample_id,
    }
    _del_dict_nones(predictions)

    return tf.estimator.EstimatorSpec(mode=mode, predictions=predictions)

def get_params():
  params = vars(FLAGS)
  params['length'] = 4*FLAGS.B*2

  if FLAGS.restore:
    with open(os.path.join(FLAGS.model_dir, 'hparams.json'), 'r') as f:
      old_params = json.load(f)
    params.update(old_params)

  return params 

def main(unparsed):
  # Using the Winograd non-fused algorithms provides a small performance boost.
  os.environ['TF_ENABLE_WINOGRAD_NONFUSED'] = '1'

  if FLAGS.mode == 'train':
    params = get_params()

    #model_fn(tf.zeros([128,40,1], dtype=tf.int32),tf.zeros([128,1]),tf.estimator.ModeKeys.TRAIN, params)

    #_log_variable_sizes(tf.trainable_variables(), "Trainable Variables")

    with open(os.path.join(FLAGS.model_dir, 'hparams.json'), 'w') as f:
      json.dump(params, f)

    # Set up a RunConfig to only save checkpoints once per training cycle.
    run_config = tf.estimator.RunConfig().replace(save_checkpoints_secs=1e9)
    estimator = tf.estimator.Estimator(
      model_fn=model_fn, model_dir=params['model_dir'], config=run_config,
      params=params)
    for _ in range(params['train_epochs'] // params['eval_frequency']):
      tensors_to_log = {
          'learning_rate': 'learning_rate',
          'cross_entropy': 'cross_entropy',#'mean_squared_error'
      }

      logging_hook = tf.train.LoggingTensorHook(
          tensors=tensors_to_log, every_n_iter=100)

      estimator.train(
          input_fn=lambda: input_fn(
              params, 'train', params['data_dir'], params['batch_size'], params['eval_frequency']),
          hooks=[logging_hook])
      
      # Evaluate the model and print results
      eval_results = estimator.evaluate(
          input_fn=lambda: input_fn(params, 'test', params['data_dir'], _NUM_SAMPLES['test']))
      tf.logging.info('Evaluation on test data set')
      print(eval_results)

  elif FLAGS.mode == 'test':
    if not os.path.exists(os.path.join(FLAGS.model_dir, 'hparams.json')):
      raise ValueError('No hparams.json found in {0}'.format(FLAGS.model_dir))
    with open(os.path.join(FLAGS.model_dir, 'hparams.json'), 'r') as f:
      params = json.load(f)
  
    estimator = tf.estimator.Estimator(
      model_fn=model_fn, model_dir=FLAGS.model_dir, params=params)
    eval_results = estimator.evaluate(
          input_fn=lambda: input_fn(params, 'test', FLAGS.data_dir, _NUM_SAMPLES['test']))
    tf.logging.info('Evaluation on test data set')
    print(eval_results)

  elif FLAGS.mode == 'predict':
    if not os.path.exists(os.path.join(FLAGS.model_dir, 'hparams.json')):
      raise ValueError('No hparams.json found in {0}'.format(FLAGS.model_dir))
    with open(os.path.join(FLAGS.model_dir, 'hparams.json'), 'r') as f:
      params = json.load(f)

    estimator = tf.estimator.Estimator(
      model_fn=model_fn, model_dir=FLAGS.model_dir, params=params)
    
    predict_from_file(estimator, FLAGS.batch_size, FLAGS.predict_from_file, FLAGS.predict_to_file)


if __name__ == '__main__':
  tf.logging.set_verbosity(tf.logging.INFO)
  FLAGS, unparsed = parser.parse_known_args()
  tf.app.run(argv=[sys.argv[0]] + unparsed)
