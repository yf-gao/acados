import sys
import os
local_path = os.path.dirname(os.path.abspath(__file__))
mpc_source_dir = os.path.join(local_path, '..')
sys.path.append(mpc_source_dir)

import numpy as np
import matplotlib.pyplot as plt
import scipy.io
from time import process_time

import casadi

from diff_drive_zoro_mpc import ZoroMPCSolver
from mpc_parameters import MPCParam

N_EXEC = 3
N_SIM = 450


def main():
    cfg_zo = MPCParam()
    zoroMPC = ZoroMPCSolver(cfg_zo)

    # Differential equation of the model
    x_int = casadi.SX.sym('x_int', cfg_zo.nx)  # x, y, theta, v, omega
    u_int = casadi.SX.sym('u_int', cfg_zo.nu)  # a, alpha
    f_x = casadi.vertcat(x_int[3]*casadi.cos(x_int[2]),
                        x_int[3]*casadi.sin(x_int[2]),
                        x_int[4],
                        u_int[0],
                        u_int[1])
    # Create an integrator
    dae = {'x': x_int, 'p': u_int, 'ode': f_x}
    opts = {'tf': cfg_zo.delta_t} # interval length
    I = casadi.integrator('I', 'rk', dae, opts)

    # Process Noise
    process_noise = np.zeros((N_SIM, cfg_zo.nx))
    """ w_bound = np.sqrt(np.diag(cfg_zo.noise_sigma_mat)) * cfg_zo.delta_t
    for idx in range(N_SIM):
        np.random.seed(idx)
        process_noise[idx,:] = np.random.normal(scale=w_bound/6) """

    # Reference trajectory
    local_path = os.path.dirname(os.path.abspath(__file__))
    mat_file_path = os.path.join(
        local_path, 'refTrajInLab.mat')
    ref_traj_mat = scipy.io.loadmat(mat_file_path)
    ref_traj_x = ref_traj_mat['filtered_states'][:-1,:]
    ref_traj_u = ref_traj_mat['filtered_inputs'][:-1,:]

    time_prep = []
    time_prop = []
    time_feedback = []
    time_sim = []
    time_qp = []

    for i_exec in range(N_EXEC):
        # closed loop mpc
        traj_zo = np.zeros((N_SIM+1, cfg_zo.nx))
        traj_zo[0,:] = ref_traj_x[0,:]
        for i_sim in range(N_SIM):
            u_opt, status = zoroMPC.solve(x_current=traj_zo[i_sim, :], \
                y_ref = np.hstack((ref_traj_x[i_sim:i_sim+cfg_zo.n_hrzn+1,:], \
                    ref_traj_u[i_sim:i_sim+cfg_zo.n_hrzn+1,:])), \
                obs_position=cfg_zo.obs_pos.flatten(), \
                    obs_radius=cfg_zo.obs_radius)

            # collect timings
            if i_exec == 0:
                time_prep.append(zoroMPC.rti_phase1_t)
                time_feedback.append(zoroMPC.rti_phase2_t)
                time_prop.append(zoroMPC.propagation_t)
                time_sim.append(zoroMPC.acados_integrator_time)
                time_qp.append(zoroMPC.acados_qp_time)
            else:
                time_prep[i_sim] = min(time_prep[i_sim], zoroMPC.rti_phase1_t)
                time_feedback[i_sim] = min(time_feedback[i_sim], zoroMPC.rti_phase2_t)
                time_prop[i_sim] = min(time_prop[i_sim], zoroMPC.propagation_t)
                time_sim[i_sim] = min(time_sim[i_sim], zoroMPC.acados_integrator_time)
                time_qp[i_sim] = min(time_qp[i_sim], zoroMPC.acados_qp_time)
            if status != 0:
                print('error status=',status,'Reset Solver')
                zoroMPC.initialized = False
            print(i_sim, u_opt, traj_zo[i_sim,:2])
            traj_zo[i_sim+1,:] = I(x0=traj_zo[i_sim, :], p=u_opt)['xf'].full().flatten()
            traj_zo[i_sim+1,:] += process_noise[i_sim,:]
            for idx_obs in range(cfg_zo.num_obs):
                if np.linalg.norm(traj_zo[i_sim+1,:2] - cfg_zo.obs_pos[idx_obs,:]) < cfg_zo.obs_radius[idx_obs]:
                    print("collision take place")
                    return False


    total_time = [time_prep[i] + time_feedback[i] + time_prop[i] for i in range(len(time_prep))]
    timing_dict = {
                   "integrator": 1e3*np.array(time_sim),
                   "preparation": 1e3*np.array(time_prep),
                   "QP": 1e3*np.array(time_qp),
                   "feedback": 1e3*np.array(time_feedback),
                   "propagation": 1e3*np.array(time_prop),
                   "total": 1e3*np.array(total_time)
                }
    plot_timings(timing_dict)

    # plot trajectory
    fig = plt.figure(1)
    plt.rcParams['font.size'] = '16'
    ax = fig.add_subplot(1,1,1)
    for idx_obs in range(cfg_zo.num_obs):
        circ = plt.Circle(cfg_zo.obs_pos[idx_obs,:], cfg_zo.obs_radius[idx_obs], \
            edgecolor="red", facecolor=(1,0,0,.5))
        ax.add_artist(circ)
    plt.plot(ref_traj_x[:, 0], ref_traj_x[:, 1], label='ref')
    plt.plot(traj_zo[:, 0], traj_zo[:, 1], label='opt sqp')
    plt.legend()
    plt.show()


def plot_timings(timing_dict):
    print("timings\t\tmin\tmean\tmax\n--------------------------------")
    for k, v in timing_dict.items():
        print(f"{k:10}\t{np.min(v):.3f}\t {np.mean(v):.3f}\t {np.max(v):.3f}")

    medianprops = dict(linestyle='-', linewidth=2.5, color='darkgreen')
    green_square = dict(markerfacecolor='palegreen', marker='D')
    plt.rcParams.update({'font.size': 16})
    _, ax = plt.subplots()
    ax.boxplot(timing_dict.values(), vert=False,
        # flierprops=green_square, \
        medianprops=medianprops, showmeans=False)
    ax.set_yticklabels(timing_dict.keys())
    plt.grid()
    plt.xlabel('CPU time [ms]')
    plt.show()

if __name__ == "__main__":
    main()