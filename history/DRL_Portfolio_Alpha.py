# -*- coding:utf-8 -*-
import tensorflow as tf
import numpy as np
import os

# This model was inspired by
# Deep Direct Reinforcement Learning for Financial Signal Representation and Trading

'''
Model interpretation:
    inputs:
    f:  shape=(batch_size, feature_number), take any information you need and make a matrix in n rows and m columns
        n is the timestep for a batch, m is the number of features. Recommend to use technical indicators (MACD,RSI...)
        of assets you want to manage.
    z:  return of rate matrix, with n time-steps and k+1 assets (k assets and your cash pool)
    c:  transaction cost
       
    formulas:
    d_t = softmax(g(f,d_t-1...d_t-n)) where g is the complex non-linear transformation procedure, here we use GRU-rnn
        Here, d_t is the action, represent the predict portfolio weight generated by current information
        and previous several actions
    r_t = d_t-1*z_t-c*|d_t-d_t-1|
        r_t is the return of current time step, which is calculated by using previous predict action d_t-1 multiplies
        the return of rate of assets price in current step. Then, subtract transaction cost if the weight of holding assets
        changes.
    R = \sum_t(log(product(r_t)))
        The total log return
    object: max(R|theta)
        The objective is to maximize the total return.
'''


class DRL_Portfolio(object):
    def __init__(self, feature_number, asset_number, object_function='sortino', dense_units_list=[1024, 512, 256], rnn_hidden_units_number=[128, 64], attn_length=30, learning_rate=0.001):
        tf.reset_default_graph()
        self.real_asset_number=asset_number
        self.f = tf.placeholder(dtype=tf.float32, shape=[None, feature_number], name='environment_features')
        self.z = tf.placeholder(dtype=tf.float32, shape=[None, asset_number], name='environment_return')
        self.c = tf.placeholder(dtype=tf.float32, shape=[], name='environment_fee')
        self.dropout_keep_prob = tf.placeholder(dtype=tf.float32, shape=[], name='dropout_keep_prob')
        self.tao = tf.placeholder(dtype=tf.float32, shape=[], name='action_temperature')
        
        with tf.variable_scope('feed_forward', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            self.dense_output = self.f
            for l in dense_units_list:
                self.dense_output = self._add_dense_layer(self.dense_output, output_shape=l, drop_keep_prob=self.dropout_keep_prob)
        
        with tf.variable_scope('rnn', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            rnn_hidden_cells = [self._add_letm_cell(i) for i in rnn_hidden_units_number]
            rnn_output_cell = self._add_letm_cell(self.real_asset_number,activation=None)
            layered_cell = tf.contrib.rnn.MultiRNNCell(rnn_hidden_cells + [rnn_output_cell])
            attention = tf.contrib.rnn.AttentionCellWrapper(cell=layered_cell, attn_length=attn_length)
            self.zero_state = layered_cell.zero_state(1, dtype=tf.float32)
            rnn_input = tf.expand_dims(self.dense_output, axis=0)
            self.rnn_outputs, self.current_state = tf.nn.dynamic_rnn(attention, inputs=rnn_input, dtype=tf.float32)
            self.current_output = tf.reshape(self.rnn_outputs[0][-1], shape=[1, self.real_asset_number])
            self.rnn_outputs = tf.concat((tf.random_uniform(shape=[1, self.real_asset_number]), tf.unstack(self.rnn_outputs, axis=0)[0]), axis=0)
        with tf.variable_scope('action', initializer=tf.contrib.layers.xavier_initializer(uniform=False), regularizer=tf.contrib.layers.l2_regularizer(0.01)):
            self.rnn_outputs = self.rnn_outputs / self.tao
            # self.cash_proportion=tf.reshape(tf.reduce_sum(self.rnn_outputs, axis=1),shape=[-1,1])
            # self.rnn_outputs=tf.concat(((1-self.cash_proportion)*self.rnn_outputs,self.cash_proportion),axis=1)
            self.action = tf.nn.softmax(self.rnn_outputs)
        with tf.variable_scope('reward'):
            self.reward_t = tf.reduce_sum(self.z * self.action[:-1] - self.c * tf.abs(self.action[1:] - self.action[:-1]), axis=1)
            self.log_reward_t = tf.log(self.reward_t)
            self.cum_reward = tf.reduce_prod(self.reward_t)
            self.cum_log_reward = tf.reduce_sum(self.log_reward_t)
            self.mean_log_reward = tf.reduce_mean(self.log_reward_t)
            self.sortino = self._sortino_ratio(self.log_reward_t, 0)
            self.sharpe = self._sharpe_ratio(self.log_reward_t, 0)
        with tf.variable_scope('train'):
            optimizer = tf.train.AdamOptimizer(learning_rate=learning_rate)
            if object_function == 'reward':
                self.train_op = optimizer.minimize(-self.mean_log_reward)
            elif object_function == 'sharpe':
                self.train_op = optimizer.minimize(-self.sharpe)
            else:
                self.train_op = optimizer.minimize(-self.sortino)
        self.init_op = tf.global_variables_initializer()
        self.saver = tf.train.Saver()
        self.session = tf.Session()
    
    def init_model(self):
        self.session.run(self.init_op)
    
    # def get_rnn_zero_state(self):
    #     zero_states = self.session.run([self.zero_state])[0]
    #     hidden_states = np.array(zero_states[:-1])
    #     output_state = zero_states[-1]
    #     return hidden_states, output_state
    
    def get_session(self):
        return self.session
    
    def _add_dense_layer(self, inputs, output_shape, drop_keep_prob, act=tf.nn.tanh):
        output = tf.contrib.layers.fully_connected(activation_fn=act, num_outputs=output_shape, inputs=inputs)
        output = tf.nn.dropout(output, drop_keep_prob)
        return output
    
    def _sortino_ratio(self, r, rf):
        mean, var = tf.nn.moments(r, axes=[0])
        sign = tf.sign(-tf.sign(r - rf) + 1)
        number = tf.reduce_sum(sign)
        lower = sign * r
        square_sum = tf.reduce_sum(tf.pow(lower, 2))
        sortino_var = tf.sqrt(square_sum / number)
        sortino = (mean - rf) / sortino_var
        return sortino
    
    def _sharpe_ratio(self, r, rf):
        mean, var = tf.nn.moments(r - rf, axes=[0])
        return mean / var
    
    def _add_gru_cell(self, units_number, activation=tf.nn.relu):
        return tf.contrib.rnn.GRUCell(num_units=units_number, activation=activation)
    
    def _add_letm_cell(self, units_number, activation=tf.nn.tanh):
        return tf.contrib.rnn.LSTMCell(activation=activation, num_units=units_number)
    
    def build_feed_dict(self, batch_F, batch_Z, keep_prob=0.8, fee=1e-3, tao=1):
        return {
            self.f: batch_F,
            self.z: batch_Z,
            self.dropout_keep_prob: keep_prob,
            self.c: fee,
            self.tao: tao
        }
    
    def change_tao(self, feed_dict, new_tao):
        feed_dict[self.tao] = new_tao
        return feed_dict
    
    def change_drop_keep_prob(self, feed_dict, new_prob):
        feed_dict[self.dropout_keep_prob] = new_prob
        return feed_dict
    
    def train(self, feed):
        self.session.run([self.train_op], feed_dict=feed)
    
    def load_model(self, model_file='./trade_model_checkpoint/trade_model'):
        self.saver.restore(self.session, model_file)
    
    def save_model(self, model_path='./trade_model_checkpoint'):
        if not os.path.exists(model_path):
            os.mkdir(model_path)
        model_file = model_path + '/trade_model'
        self.saver.save(self.session, model_file)
    
    def trade(self, feed):
        rewards, cum_log_reward, cum_reward, actions = self.session.run([self.reward_t, self.cum_log_reward, self.cum_reward, self.action], feed_dict=feed)
        return rewards, cum_log_reward, cum_reward, actions
