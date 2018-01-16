import tensorflow as tf
import numpy as np
import cv2
import argparse
import ConfigParser
import shutil
import os
import sys
import time
import matplotlib.pyplot as plt


from ae import factory
from ae import utils as u
from eval import eval_utils, eval_plots, latex_report
from sixd_toolkit.pysixd import inout
from sixd_toolkit.params import dataset_params
from sixd_toolkit.tools import eval_calc_errors, eval_loc

def main():
    
    parser = argparse.ArgumentParser()
    
    parser.add_argument('experiment_name')
    parser.add_argument('evaluation_name')
    arguments = parser.parse_args()
    full_name = arguments.experiment_name.split('/')
    experiment_name = full_name.pop()
    experiment_group = full_name.pop() if len(full_name) > 0 else ''
    evaluation_name = arguments.evaluation_name

    workspace_path = os.environ.get('AE_WORKSPACE_PATH')
    train_cfg_file_path = u.get_config_file_path(workspace_path, experiment_name, experiment_group)
    eval_cfg_file_path = u.get_eval_config_file_path(workspace_path)

    train_args = ConfigParser.ConfigParser()
    eval_args = ConfigParser.ConfigParser()
    train_args.read(train_cfg_file_path)
    eval_args.read(eval_cfg_file_path)
    
    #[DATA]
    dataset_name = eval_args.get('DATA','DATASET')
    scenes = eval(eval_args.get('DATA','SCENES'))
    objects = eval(eval_args.get('DATA','OBJECTS'))
    cam_type = eval_args.get('DATA','CAM_TYPE')
    data_params = dataset_params.get_dataset_params(dataset_name, model_type='', train_type='', test_type=cam_type, cam_type=cam_type)
    #[BBOXES]
    estimate_bbs = eval_args.getboolean('BBOXES', 'ESTIMATE_BBS')
    single_instance = eval_args.getboolean('BBOXES', 'SINGLE_INSTANCE')
    #[METRIC]
    top_n = eval_args.getint('METRIC','TOP_N')

    eval_dir = u.get_eval_dir(workspace_path, experiment_group, experiment_name, evaluation_name, dataset_name, cam_type)
    log_dir = u.get_log_dir(workspace_path, experiment_name, experiment_group)
    if not os.path.exists(eval_dir):
        os.makedirs(eval_dir)
    shutil.copy2(eval_cfg_file_path, eval_dir)

    # #[SSD]
    # num_classes = ssd_train_args.getint('SSD','NUM_CLASSES')
    print eval_args
    # print eval_dir
    # eval_calc_errors.eval_calc_errors(eval_args, eval_dir)
    # exit()

    codebook, dataset, decoder = factory.build_codebook_from_name(experiment_name, experiment_group, return_dataset = True, return_decoder = True)

    with tf.Session() as sess:
        factory.restore_checkpoint(sess, tf.train.Saver(), experiment_name, experiment_group)
        if estimate_bbs:
            #Object Detection, seperate from main
            sys.path.append('/net/rmc-lx0050/home_local/sund_ma/src/SSD_Tensorflow')
            from ssd_detector import SSD_detector
            #TODO: set num_classes, network etc.
            ssd = SSD_detector(sess, num_classes=31, net_shape=(300,300))

        for scene_id in scenes:
            scene_res_dir = os.path.join(eval_dir, '{scene_id:02d}'.format(scene_id = scene_id))
            if not os.path.exists(scene_res_dir):
                os.makedirs(scene_res_dir)

            if estimate_bbs:
                test_imgs = eval_utils.load_scenes(scene_id, eval_args)
                bb_preds = {}
                for i,img in enumerate(test_imgs):
                    bb_preds[i] = ssd.detectSceneBBs(img, min_score=.3, nms_threshold=.45)
                # inout.save_yaml(os.path.join(scene_res_dir,'bb_preds.yml'), bb_preds)
                test_img_crops, bbs, bb_scores, visibilities = eval_utils.generate_scene_crops(test_imgs, bb_preds, eval_args, train_args)
            else:
                test_img_crops, bbs, bb_scores, visibilities = eval_utils.get_gt_scene_crops(scene_id, eval_args, train_args)
                

            info = inout.load_info(data_params['scene_info_mpath'].format(scene_id))
            Ks_test = [np.array(v['cam_K']).reshape(3,3) for v in info.values()]

            ######remove
            gts = inout.load_gt(data_params['scene_gt_mpath'].format(scene_id))
            # visib_gt = inout.load_yaml(data_params['scene_gt_stats_mpath'].format(scene_id, 15))
            #######

            for obj_id in objects:

                noof_scene_views = eval_utils.noof_scene_views(scene_id, eval_args)

                t_errors = []
                test_embeddings = []

                for view in xrange(noof_scene_views):
                    try:
                        test_crops, test_bbs, test_scores = eval_utils.select_img_crops(test_img_crops[view][obj_id], bbs[view][obj_id],
                                                                                        bb_scores[view][obj_id], visibilities[view][obj_id], eval_args)
                    except:
                        pass

                    preds = {}
                    pred_views = []
                    
                    for test_crop, test_bb, test_score in zip(test_crops, test_bbs, test_scores):    

                        start = time.time()
                        Rs_est, ts_est, normed_test_code = codebook.nearest_rotation_with_bb_depth(sess, test_crop, test_bb, Ks_test[view], top_n, train_args)
                        ae_time = time.time() - start
                        test_embeddings.append(normed_test_code)

                        for gt in gts[view]:
                            if gt['obj_id'] == obj_id:
                                print gt['cam_t_m2c'].squeeze()
                                t_errors.append(ts_est[0]-gt['cam_t_m2c'].squeeze())

                        print ts_est
                        print Rs_est

                        run_time = ae_time + bb_preds[view][0]['ssd_time'] if estimate_bbs else ae_time
                        if top_n > 1:
                            for p in xrange(top_n):
                                preds.setdefault('ests',[]).append({'score':test_score, 'R': Rs_est[p], 't':ts_est[p]})
                        else:
                            preds.setdefault('ests',[]).append({'score':test_score, 'R': Rs_est, 't':ts_est})
                                                
                        if eval_args.getboolean('PLOT','RECONSTRUCTION'):
                            eval_plots.plot_reconstruction(sess, decoder, normed_test_code)
                        if eval_args.getboolean('PLOT','NEAREST_NEIGHBORS'):
                            for R_est in Rs_est:
                                pred_views.append(dataset.render_rot( R_est ,downSample = 2))
                            eval_plots.show_nearest_rotation(pred_views, test_crop)


                    # save predictions in sixd format
                    res_path = os.path.join(scene_res_dir,'%04d_%02d.yml' % (view,obj_id))
                    inout.save_results_sixd17(res_path, preds, run_time=run_time)
                
                # calculate 6D pose errors
                if eval_args.getboolean('EVALUATION','COMPUTE_ERRORS'):
                    eval_calc_errors.eval_calc_errors(eval_args, eval_dir)
                if eval_args.getboolean('EVALUATION','EVALUATE_ERRORS'):    
                    eval_loc.match_and_eval_performance_scores(eval_args, eval_dir)


                cyclo = train_args.getint('Embedding','NUM_CYCLO')
                if not os.path.exists(os.path.join(eval_dir,'latex')):
                    os.makedirs(os.path.join(eval_dir,'latex'))
                if not os.path.exists(os.path.join(eval_dir,'figures')):
                    os.makedirs(os.path.join(eval_dir,'figures'))
                if eval_args.getboolean('PLOT','EMBEDDING_PCA'):
                    embedding = sess.run(codebook.embedding_normalized)
                    eval_plots.compute_pca_plot_embedding(eval_dir, embedding[::cyclo], np.array(test_embeddings))
                if eval_args.getboolean('PLOT','VIEWSPHERE'):
                    eval_plots.plot_viewsphere_for_embedding(dataset.viewsphere_for_embedding[::cyclo], eval_dir)
                if eval_args.getboolean('PLOT','CUM_T_ERROR_HIST'):
                    eval_plots.plot_t_err_hist(t_errors, eval_dir)
                if eval_args.getboolean('PLOT','CUM_R_ERROR_HIST'):
                    eval_plots.plot_R_err_hist(top_n, eval_dir, scene_id)
                if eval_args.getboolean('PLOT','ANIMATE_EMBEDDING_PCA'):
                    eval_plots.animate_embedding_path(test_embeddings)
                # plt.show()


        # calculate 6D pose errors
        # print 'exiting ...'
        # eval_calc_errors.eval_calc_errors(eval_args, eval_dir)

    report = latex_report.Report(eval_dir)
    report.write_configuration(train_cfg_file_path,eval_cfg_file_path)
    report.merge_all_tex_files()
    report.include_all_figures()
    report.save()

if __name__ == '__main__':
    main()
