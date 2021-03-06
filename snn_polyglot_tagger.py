from __future__ import unicode_literals

import io
import re
import sys
import math
import string
import random
import pickle
from argparse import ArgumentParser
from collections import Counter, defaultdict

import dynet as dy
import numpy as np
from gensim.models.word2vec import Word2Vec

class Meta:
    def __init__(self):
        self.c_dim = 32  # character-rnn input dimension
        self.add_words = 1  # additional lookup for missing/special words
        self.n_hidden = 64  # pos-mlp hidden layer dimension
        self.lstm_char_dim = 64  # char-LSTM output dimension
        self.lstm_word_dim = 128  # LSTM (word-char concatenated input) output dimension
        ############################ STACKING-MODEL-DIMS ##############################
        self.xc_dim = 32
        self.xn_hidden = 64
        self.xlstm_char_dim = 64
        self.xlstm_word_dim = 128


class POSTagger():
    def __init__(self, model=None, meta=None, new_meta=None, test=False):
        self.model = dy.Model()
        if new_meta:
            self.meta = new_meta
        else:
            self.meta = pickle.load(open('%s.meta' %model, 'rb')) if model else meta
        self.EWORDS_LOOKUP = self.model.add_lookup_parameters((self.meta.n_words_eng, self.meta.w_dim_eng))
        self.HWORDS_LOOKUP = self.model.add_lookup_parameters((self.meta.n_words_hin, self.meta.w_dim_hin))
        if not model:
            for word, V in ewvm.vocab.iteritems():
                self.EWORDS_LOOKUP.init_row(V.index+self.meta.add_words, ewvm.syn0[V.index])
            for word, V in hwvm.vocab.iteritems():
                self.HWORDS_LOOKUP.init_row(V.index+self.meta.add_words, hwvm.syn0[V.index])

        self.ECHARS_LOOKUP = self.model.add_lookup_parameters((self.meta.n_chars_eng, self.meta.c_dim))
        self.HCHARS_LOOKUP = self.model.add_lookup_parameters((self.meta.n_chars_hin, self.meta.c_dim))

        # MLP on top of biLSTM outputs 100 -> 32 -> ntags
        self.W1 = self.model.add_parameters((self.meta.n_hidden, self.meta.lstm_word_dim*2))
        self.W2 = self.model.add_parameters((self.meta.n_tags, self.meta.n_hidden))
        self.B1 = self.model.add_parameters(self.meta.n_hidden)
        self.B2 = self.model.add_parameters(self.meta.n_tags)

        # word-level LSTMs
        self.fwdRNN = dy.LSTMBuilder(1, self.meta.w_dim_eng+self.meta.lstm_char_dim*2, self.meta.lstm_word_dim, self.model) 
        self.bwdRNN = dy.LSTMBuilder(1, self.meta.w_dim_eng+self.meta.lstm_char_dim*2, self.meta.lstm_word_dim, self.model)
        self.fwdRNN2 = dy.LSTMBuilder(1, self.meta.lstm_word_dim*2, self.meta.lstm_word_dim, self.model) 
        self.bwdRNN2 = dy.LSTMBuilder(1, self.meta.lstm_word_dim*2, self.meta.lstm_word_dim, self.model)

        # char-level LSTMs
        self.ecfwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.lstm_char_dim, self.model)
        self.ecbwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.lstm_char_dim, self.model)
        self.hcfwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.lstm_char_dim, self.model)
        self.hcbwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.lstm_char_dim, self.model)
        if not test and model:
            self.model.populate('%s.dy' %model)

        ################################### STACKING ########################################
        # MLP on top of biLSTM outputs 100 -> 32 -> ntags
        self.xW1 = self.model.add_parameters((self.meta.xn_hidden, self.meta.xlstm_word_dim*2))
        self.xW2 = self.model.add_parameters((self.meta.xn_tags, self.meta.xn_hidden))
        self.xB1 = self.model.add_parameters(self.meta.xn_hidden)
        self.xB2 = self.model.add_parameters(self.meta.xn_tags)

        # word-level LSTMs
        self.xfwdRNN = dy.LSTMBuilder(1, self.meta.w_dim_eng+self.meta.xlstm_char_dim*2+self.meta.n_hidden,
                                        self.meta.xlstm_word_dim, self.model)
        self.xbwdRNN = dy.LSTMBuilder(1, self.meta.w_dim_eng+self.meta.xlstm_char_dim*2+self.meta.n_hidden,
                                        self.meta.xlstm_word_dim, self.model)
        self.xfwdRNN2 = dy.LSTMBuilder(1, self.meta.xlstm_word_dim*2, self.meta.xlstm_word_dim, self.model) 
        self.xbwdRNN2 = dy.LSTMBuilder(1, self.meta.xlstm_word_dim*2, self.meta.xlstm_word_dim, self.model)

        # char-level LSTMs
        self.xecfwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.xlstm_char_dim, self.model)
        self.xecbwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.xlstm_char_dim, self.model)
        self.xhcfwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.xlstm_char_dim, self.model)
        self.xhcbwdRNN = dy.LSTMBuilder(1, self.meta.c_dim, self.meta.xlstm_char_dim, self.model)
        if test and model:
            self.model.populate('%s.dy' %model)


    def word_rep(self, word, lang='en'):
        if not self.eval and random.random() < 0.3:
            return self.HWORDS_LOOKUP[0] if lang=='hi' else self.EWORDS_LOOKUP[0]
        if lang == 'hi':
            idx = self.meta.hw2i.get(word, 0)
            return self.HWORDS_LOOKUP[idx]
        elif lang == 'en':
            idx = self.meta.ew2i.get(word, self.meta.ew2i.get(word.lower(), 0))
            return self.EWORDS_LOOKUP[idx]
    
    def char_rep_hin(self, w, f, b):
        no_c_drop = False
        if self.eval or random.random()<0.9:
            no_c_drop = True
        bos, eos, unk = self.meta.hc2i["bos"], self.meta.hc2i["eos"], self.meta.hc2i["unk"]
        char_ids = [bos] + [self.meta.hc2i.get(c, unk) if no_c_drop else unk for c in w] + [eos]
        char_embs = [self.HCHARS_LOOKUP[cid] for cid in char_ids]
        fw_exps = f.transduce(char_embs)
        bw_exps = b.transduce(reversed(char_embs))
        return dy.concatenate([ fw_exps[-1], bw_exps[-1] ])
    
    def char_rep_eng(self, w, f, b):
        no_c_drop = False
        if self.eval or random.random()<0.9:
            no_c_drop = True
        bos, eos, unk = self.meta.ec2i["bos"], self.meta.ec2i["eos"], self.meta.ec2i["unk"]
        char_ids = [bos] + [self.meta.ec2i.get(c, unk) if no_c_drop else unk for c in w] + [eos]
        char_embs = [self.ECHARS_LOOKUP[cid] for cid in char_ids]
        fw_exps = f.transduce(char_embs)
        bw_exps = b.transduce(reversed(char_embs))
        return dy.concatenate([ fw_exps[-1], bw_exps[-1] ])

    def char_rep(self, word, hf, hb, ef, eb, lang='en'):
        if lang == 'hi':
            return self.char_rep_hin(word, hf, hb)
        elif lang == 'en':
            return self.char_rep_eng(word, ef, eb)

    def enable_dropout(self):
        self.fwdRNN.set_dropout(0.3)
        self.bwdRNN.set_dropout(0.3)
        self.fwdRNN2.set_dropout(0.3)
        self.bwdRNN2.set_dropout(0.3)
        self.ecfwdRNN.set_dropout(0.3)
        self.ecbwdRNN.set_dropout(0.3)
        self.hcfwdRNN.set_dropout(0.3)
        self.hcbwdRNN.set_dropout(0.3)
        self.xfwdRNN.set_dropout(0.3)
        self.xbwdRNN.set_dropout(0.3)
        self.xfwdRNN2.set_dropout(0.3)
        self.xbwdRNN2.set_dropout(0.3)
        self.xecfwdRNN.set_dropout(0.3)
        self.xecbwdRNN.set_dropout(0.3)
        self.xhcfwdRNN.set_dropout(0.3)
        self.xhcbwdRNN.set_dropout(0.3)
        self.w1 = dy.dropout(self.w1, 0.3)
        self.b1 = dy.dropout(self.b1, 0.3)
        self.xw1 = dy.dropout(self.xw1, 0.3)
        self.xb1 = dy.dropout(self.xb1, 0.3)

    def disable_dropout(self):
        self.fwdRNN.disable_dropout()
        self.bwdRNN.disable_dropout()
        self.fwdRNN2.disable_dropout()
        self.bwdRNN2.disable_dropout()
        self.ecfwdRNN.disable_dropout()
        self.ecbwdRNN.disable_dropout()
        self.hcfwdRNN.disable_dropout()
        self.hcbwdRNN.disable_dropout()
        self.xfwdRNN.disable_dropout()
        self.xbwdRNN.disable_dropout()
        self.xfwdRNN2.disable_dropout()
        self.xbwdRNN2.disable_dropout()
        self.xecfwdRNN.disable_dropout()
        self.xecbwdRNN.disable_dropout()
        self.xhcfwdRNN.disable_dropout()
        self.xhcbwdRNN.disable_dropout()

    def build_tagging_graph(self, words, ltags):
        # parameters -> expressions
        self.w1 = dy.parameter(self.W1)
        self.b1 = dy.parameter(self.B1)
        self.w2 = dy.parameter(self.W2)
        self.b2 = dy.parameter(self.B2)
        self.xw1 = dy.parameter(self.xW1)
        self.xb1 = dy.parameter(self.xB1)
        self.xw2 = dy.parameter(self.xW2)
        self.xb2 = dy.parameter(self.xB2)

        # apply dropout
        if self.eval:
            self.disable_dropout()
        else:
            self.enable_dropout()

        # initialize the RNNs
        f_init = self.fwdRNN.initial_state()
        b_init = self.bwdRNN.initial_state()
        f2_init = self.fwdRNN2.initial_state()
        b2_init = self.bwdRNN2.initial_state()
    
        self.hcf_init = self.hcfwdRNN.initial_state()
        self.hcb_init = self.hcbwdRNN.initial_state()
    
        self.ecf_init = self.ecfwdRNN.initial_state()
        self.ecb_init = self.ecbwdRNN.initial_state()

        xf_init = self.xfwdRNN.initial_state()
        xb_init = self.xbwdRNN.initial_state()
        xf2_init = self.xfwdRNN2.initial_state()
        xb2_init = self.xbwdRNN2.initial_state()
    
        self.xhcf_init = self.xhcfwdRNN.initial_state()
        self.xhcb_init = self.xhcbwdRNN.initial_state()
    
        self.xecf_init = self.xecfwdRNN.initial_state()
        self.xecb_init = self.xecbwdRNN.initial_state()

        # get the word vectors. word_rep(...) returns a 128-dim vector expression for each word.
        wembs = [self.word_rep(w, l) for w,l in zip(words, ltags)]
        cembs = [self.char_rep(w, self.hcf_init, self.hcb_init,
                    self.ecf_init, self.ecb_init, l) for w,l in zip(words, ltags)]
        xembs = [dy.concatenate([w, c]) for w,c in zip(wembs, cembs)]
    
        # feed word vectors into biLSTM
        fw_exps = f_init.transduce(xembs)
        bw_exps = b_init.transduce(reversed(xembs))
    
        # biLSTM states
        bi_exps = [dy.concatenate([f,b]) for f,b in zip(fw_exps, reversed(bw_exps))]

        # feed word vectors into biLSTM
        fw_exps = f2_init.transduce(bi_exps)
        bw_exps = b2_init.transduce(reversed(bi_exps))
    
        # biLSTM states
        bi_exps = [dy.concatenate([f,b]) for f,b in zip(fw_exps, reversed(bw_exps))]
    
        # feed each biLSTM state to an MLP
        exps = []
        pos_hidden = []
        for xi in bi_exps:
            xh = self.w1 * xi
            #xh = self.meta.activation(xh) + self.b1
            pos_hidden.append(xh)

        cembs = [self.char_rep(w, self.xhcf_init, self.xhcb_init, self.xecf_init, self.xecb_init, l) for w,l in zip(words, ltags)]
        xembs = [dy.concatenate([w, c, p]) for w, c,p in zip(wembs, cembs, pos_hidden)]
        xfw_exps = xf_init.transduce(xembs)
        xbw_exps = xb_init.transduce(reversed(xembs))
    
        # biLSTM states
        bi_exps = [dy.concatenate([f,b]) for f,b in zip(xfw_exps, reversed(xbw_exps))]

        # feed word vectors into biLSTM
        fw_exps = xf2_init.transduce(bi_exps)
        bw_exps = xb2_init.transduce(reversed(bi_exps))
    
        # biLSTM states
        bi_exps = [dy.concatenate([f,b]) for f,b in zip(fw_exps, reversed(bw_exps))]

        exps = []
        for xi in bi_exps:
            xh = self.xw1 * xi
            xh = self.meta.activation(xh) + self.xb1
            xo = self.xw2*xh + self.xb2
            exps.append(xo)
    
        return exps
    
    def sent_loss(self, words, tags, ltags):
        self.eval = False
        vecs = self.build_tagging_graph(words, ltags)
        for v,t in zip(vecs,tags):
            tid = self.meta.t2i[t]
            err = dy.pickneglogsoftmax(v, tid)
            self.loss.append(err)
    
    def tag_sent(self, words, ltags):
        self.eval = True
        dy.renew_cg()
        vecs = self.build_tagging_graph(words, ltags)
        vecs = [dy.softmax(v) for v in vecs]
        probs = [v.npvalue() for v in vecs]
        tags = []
        for prb in probs:
            tag = np.argmax(prb)
            tags.append(self.meta.i2t[tag])
        return zip(words, tags)

def read(fname, lang=None):
    data = []
    sent = []
    pid = 3 if args.ud else 4
    fp = io.open(fname, encoding='utf-8')
    for i,line in enumerate(fp):
        line = line.split()
        if not line:
            data.append(sent)
            sent = []
        else:
            try:
                w,p,l = line
            except ValueError:
                try:
                    w,p,l = line[2], line[pid], line[8]
                except Exception:
                    sys.stderr.write('Wrong file format\n')
                    sys.exit(1)
            l = l.split('|')[0]
            l = 'hi' if l=='hi' else 'en'
            sent.append((w,p,l))
    if sent: data.append(sent)
    return data

def eval(dev, ofp=None):
    good_sent = bad_sent = good = bad = 0.0
    gall, pall = [], []
    for sent in dev:
        words, golds, ltags = zip(*sent)
        tags = [t for w,t in tagger.tag_sent(words, ltags)]
        #pall.extend(tags)
        if list(tags) == list(golds): good_sent += 1
        else: bad_sent += 1
        for go,gu in zip(golds,tags):
            if go == gu: good += 1
            else: bad += 1
    #print(cr(gall, pall, digits=4))
    print(good/(good+bad), good_sent/(good_sent+bad_sent))
    return good/(good+bad)

def train_tagger(train):
    pr_acc = 0.0
    n_samples = len(train)
    num_tagged, cum_loss = 0, 0
    for ITER in xrange(args.iter):
        dy.renew_cg()
        tagger.loss = []
        random.shuffle(train)
        for i,s in enumerate(train, 1):
            if i % 500 == 0 or i == n_samples:   # print status
                trainer.status()
                print(cum_loss / num_tagged)
                cum_loss, num_tagged = 0, 0
            words, golds, ltags = zip(*s)
            tagger.sent_loss(words, golds, ltags)
            num_tagged += len(golds)
            if len(tagger.loss) > 50:
                batch_loss = dy.esum(tagger.loss)
                cum_loss += batch_loss.scalar_value()
	        batch_loss.backward()
                trainer.update()
                tagger.loss = []
                dy.renew_cg()
        if tagger.loss:
            batch_loss = dy.esum(tagger.loss)
            cum_loss += batch_loss.scalar_value()
	    batch_loss.backward()
            trainer.update()
            tagger.loss = []
            dy.renew_cg()
        print("epoch %r finished" % ITER)
        sys.stdout.flush()
        if args.cdev:
            new_acc = eval(cdev)
        if new_acc > pr_acc:
            pr_acc = new_acc
            print('Save Point:: %d' %ITER)
            if args.save_model:
                tagger.model.save('%s.dy' %args.save_model)


def get_char_map(data):
    tags = set()
    for sent in data:
        for w,p,l in sent:
            tags.add(p)
    meta.xn_tags = len(tags)
    meta.i2t = dict(enumerate(tags))
    meta.t2i = {t:i for i,t in meta.i2t.items()}


if __name__ == '__main__':
    parser = ArgumentParser(description="POS Tagger")
    group = parser.add_mutually_exclusive_group()
    parser.add_argument('--dynet-gpu')
    parser.add_argument('--dynet-mem')
    parser.add_argument('--dynet-devices')
    parser.add_argument('--dynet-autobatch')
    parser.add_argument('--dynet-seed', dest='seed', type=int, default='127')
    parser.add_argument('--ctrain', help='Hindi-English CS CONLL/TNT Train file')
    parser.add_argument('--cdev', help='Hindi-English CS CONLL/TNT Dev/Test file')
    parser.add_argument('--trainer', default='momsgd', help='Trainer [momsgd|adam|adadelta|adagrad]')
    parser.add_argument('--activation-fn', dest='act_fn', default='tanh', help='Activation function [tanh|rectify|logistic]')
    parser.add_argument('--ud', type=int, default=1, help='1 if UD treebank else 0')
    parser.add_argument('--iter', type=int, default=100, help='No. of Epochs')
    parser.add_argument('--bvec', type=int, help='1 if binary embedding file else 0')
    parser.add_argument('--base-model', dest='base_model', help='build a stacking model on this pretrained model')
    group.add_argument('--save-model', dest='save_model', help='Specify path to save model')
    group.add_argument('--load-model', dest='load_model', help='Load Pretrained Model')
    args = parser.parse_args()
    np.random.seed(args.seed)
    random.seed(args.seed)

    if args.cdev:
        cdev = read(args.cdev)
    if not args.load_model:
        xmeta = Meta()
        meta = pickle.load(open('%s.meta' %args.base_model, 'rb'))
        train = read(args.ctrain)
        
        get_char_map(train)
        meta.xc_dim = xmeta.xc_dim
        meta.xn_hidden = xmeta.xn_hidden
        meta.xlstm_word_dim = xmeta.xlstm_word_dim
        meta.xlstm_char_dim = xmeta.xlstm_char_dim

        trainers = {
            'momsgd'   : dy.MomentumSGDTrainer,
            'adam'     : dy.AdamTrainer,
            'simsgd'   : dy.SimpleSGDTrainer,
            'adagrad'  : dy.AdagradTrainer,
            'adadelta' : dy.AdadeltaTrainer
            }
        act_fn = {
            'sigmoid' : dy.logistic,
            'tanh'    : dy.tanh,
            'relu'    : dy.rectify,
            }
        meta.trainer = trainers[args.trainer]
        meta.activation = act_fn[args.act_fn] 

    if args.save_model:
        pickle.dump(meta, open('%s.meta' %args.save_model, 'wb'))
    if args.load_model:
        sys.stderr.write('Loading Models ...\n')
        tagger = POSTagger(model=args.load_model, test=True)
        if args.cdev:
            eval(cdev) 
        sys.stderr.write('Done!\n')
    elif args.base_model:
        tagger = POSTagger(model=args.base_model, new_meta=meta)
        trainer = meta.trainer(tagger.model)
        train_tagger(train)
