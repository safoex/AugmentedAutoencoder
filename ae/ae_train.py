# -*- coding: utf-8 -*-
import os
import ConfigParser
import argparse
import numpy as np
import signal
import shutil
import time

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import tensorflow as tf
import progressbar

import ae_factory as factory
import utils as u

def main():
    workspace_path = os.environ.get('AE_WORKSPACE_PATH')

    if workspace_path == None:
        print 'Please define a workspace path:\n'
        print 'export AE_WORKSPACE_PATH=/path/to/workspace\n'
        exit(-1)

    gentle_stop = np.array((1,), dtype=np.bool)
    gentle_stop[0] = False
    def on_ctrl_c(signal, frame):
        gentle_stop[0] = True
    signal.signal(signal.SIGINT, on_ctrl_c)

    parser = argparse.ArgumentParser()
    parser.add_argument("experiment_name")
    parser.add_argument("-d", action='store_true', default=False)
    parser.add_argument("-gen", action='store_true', default=False)
    arguments = parser.parse_args()

    full_name = arguments.experiment_name.split('/')
    
    experiment_name = full_name.pop()
    experiment_group = full_name.pop() if len(full_name) > 0 else ''
    
    debug_mode = arguments.d
    generate_data = arguments.gen

    cfg_file_path = u.get_config_file_path(workspace_path, experiment_name, experiment_group)
    checkpoint_file = u.get_checkpoint_basefilename(workspace_path, experiment_name, experiment_group)
    ckpt_dir = u.get_checkpoint_dir(workspace_path, experiment_name, experiment_group)
    log_dir = u.get_log_dir(workspace_path, experiment_name, experiment_group)
    dataset_path = u.get_dataset_path(workspace_path)

    if not os.path.exists(cfg_file_path):
        print 'Could not find config file:\n'
        print '{}\n'.format(cfg_file_path)
        exit(-1)

    args = ConfigParser.ConfigParser()
    args.read(cfg_file_path)


    if not os.path.exists(ckpt_dir):
        os.makedirs(ckpt_dir)
    if not os.path.exists(dataset_path):
        os.makedirs(dataset_path)
        
    shutil.copy2(cfg_file_path, log_dir)

    with tf.variable_scope(experiment_name):
        dataset = factory.build_dataset(dataset_path, args)
        queue = factory.build_queue(dataset, args)
        encoder = factory.build_encoder(queue.x, args)
        decoder = factory.build_decoder(queue.y, encoder, args)
        ae = factory.build_ae(encoder, decoder)
        optimize = factory.build_optimizer(ae, args)
        codebook = factory.build_codebook(encoder, dataset, args)
        saver = tf.train.Saver()

    dataset.get_training_images(dataset_path, args)
    dataset.load_bg_images(dataset_path)

    if generate_data:
        print 'finished generating synthetic training data for ' + experiment_name
        print 'exiting...'
        exit()

    num_iter = args.getint('Training', 'NUM_ITER')
    save_interval = args.getint('Training', 'SAVE_INTERVAL')

    bar = progressbar.ProgressBar(
        maxval=num_iter, 
        widgets=[' [', progressbar.Timer(), ' | ', progressbar.Counter('%0{}d / {}'.format(len(str(num_iter)), num_iter)), ' ] ', 
        progressbar.Bar(), 
        ' (', progressbar.ETA(), ') ']
    )

    with tf.Session() as sess:

        chkpt = tf.train.get_checkpoint_state(ckpt_dir)
        if chkpt and chkpt.model_checkpoint_path:
            saver.restore(sess, chkpt.model_checkpoint_path)
        else:
            sess.run(tf.global_variables_initializer())

        writer = tf.summary.FileWriter(ckpt_dir, sess.graph)

        
        if not debug_mode:
            print 'Training with %s model' % args.get('Dataset','MODEL'), os.path.basename(args.get('Paths','MODEL_PATH'))
            bar.start()
            
        queue.start(sess)
        for i in xrange(ae.global_step.eval(), num_iter):
            if not debug_mode:
                
                #b=time.time()
                sess.run(optimize)
                #print 'forward backward time ', time.time()-b

                bar.update(i)
                if (i+1) % save_interval == 0:
                    saver.save(sess, checkpoint_file, global_step=ae.global_step)
            else:
                this_x, this_y = sess.run([queue.x, queue.y])
                import cv2; import gl_utils as gu
                cv2.imshow('sample batch', np.hstack( (gu.tiles(this_x, 5, 5), gu.tiles(this_y, 5, 5))) )
                k = cv2.waitKey(0)
                if k == 27:
                    break

            if gentle_stop[0]:
                break

        queue.stop(sess)
        if not debug_mode:
            bar.finish()
        if not gentle_stop[0] and not debug_mode:
            print 'To create the embedding run:\n'
            print 'ae_embed {}\n'.format(full_name)

if __name__ == '__main__':
    main()
    