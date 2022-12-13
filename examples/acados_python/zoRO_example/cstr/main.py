import sys
import os
from xmlrpc.client import Boolean
local_path = os.path.dirname(os.path.abspath(__file__))
cstr_source_dir = os.path.join(local_path, '..', '..', 'cstr')
sys.path.append(cstr_source_dir)
zoro_source_dir = os.path.join(local_path, '..')
sys.path.append(zoro_source_dir)

import numpy as np
import matplotlib
from typing import Optional

from cstr_model import CSTRParameters, setup_cstr_model, setup_linearized_model
from zoro_ocp_solver import MpcCSTRParameters, DistCSTRParameters, setup_acados_ocp_solver, AcadosOcpSolver
from setup_acados_integrator import setup_acados_integrator, AcadosSimSolver
from cstr_utils import plot_cstr, compute_lqr_gain


def samplesFromEllipsoid(N, w, Z)->np.ndarray:
    """
    draws samples from ellipsoid with center w and variability matrix Z
    """

    np.random.seed(1)

    nw = w.shape[0]                  # dimension
    lam, v = np.linalg.eig(Z)

    # sample in hypersphere
    r = np.random.rand()**(1/nw)     # radial position of sample
    x = np.random.randn(N, nw)
    y = np.zeros((N, nw))
    for idx in range(N):
        x[idx,:] = x[idx,:] / np.linalg.norm(x[idx,:])
        x[idx,:] *= r
        # project to ellipsoid
        y[idx,:] = v @ (np.sqrt(lam) * x[idx,:]) + w

    return y


def simulate(
    controller: Optional[AcadosOcpSolver],
    plant: AcadosSimSolver,
    x0: np.ndarray,
    Nsim: int,
    X_ref: np.ndarray,
    U_ref: np.ndarray,
    cstr_tightening: Boolean,
    dist_samples: np.ndarray,
    ubx: np.ndarray
):

    nx = X_ref.shape[1]
    nu = U_ref.shape[1]

    X = np.ndarray((Nsim + 1, nx))
    U = np.ndarray((Nsim, nu))
    timings_solver = np.zeros((Nsim))
    timings_integrator = np.zeros((Nsim))

    Nexec = 10

    # closed loop
    xcurrent = x0
    X[0, :] = xcurrent

    for i_exec in range(Nexec):
        # Reset the controller
        xcurrent = x0
        controller.reset()
        for i_stage in range(0, controller.acados_ocp.dims.N):
            controller.set(i_stage, 'u', np.zeros((controller.acados_ocp.dims.nu,)))
            controller.set(i_stage, 'x', x0)

        for i_sim in range(Nsim):

            # set initial state
            controller.set(0, "lbx", xcurrent)
            controller.set(0, "ubx", xcurrent)

            yref = np.concatenate((X_ref[i_sim, :], U_ref[i_sim, :]))
            for stage in range(controller.acados_ocp.dims.N):
                controller.set(stage, "yref", yref)
            controller.set(controller.acados_ocp.dims.N, "yref", X_ref[i_sim, :])

            if cstr_tightening:
                controller.custom_update([])

            # solve ocp
            status = controller.solve()

            if status != 0:
                controller.print_statistics()
                raise Exception(
                    f"acados controller returned status {status} in simulation step {i_sim}. Exiting."
                )

            U[i_sim, :] = controller.get(0, "u")
            if i_exec ==0:
                timings_solver[i_sim] = controller.get_stats("time_tot")
            else:
                timings_solver[i_sim] = min(controller.get_stats("time_tot"), timings_solver[i_sim])

            # simulate system
            plant.set("x", xcurrent)
            plant.set("u", U[i_sim, :])

            if plant.acados_sim.solver_options.integrator_type == "IRK":
                plant.set("xdot", np.zeros((nx,)))

            status = plant.solve()
            if status != 0:
                raise Exception(
                    f"acados integrator returned status {status} in simulation step {i_sim}. Exiting."
                )

            if i_exec == 0:
                timings_integrator[i_sim] = plant.get("time_tot")
            else:
                timings_integrator[i_sim] = min(plant.get("time_tot"), timings_integrator[i_sim])

            # update state
            xcurrent = plant.get("x") + dist_samples[i_sim,:]
            X[i_sim + 1, :] = xcurrent

            # if exceeds the upper bound of the state constraints
            if (xcurrent > ubx).any():
                X[(i_sim+1):, :] = np.nan
                U[min(i_sim+1, Nsim):, :] = np.nan
                timings_solver[min(i_sim+1, Nsim):] = np.nan
                timings_integrator[min(i_sim+1, Nsim):] = np.nan
                print("exceed the upper bound at i_sim=", i_sim)
                break

    return X, U, timings_solver, timings_integrator



def main():

    Tsim = 25
    dt_plant = 0.25  # [min]

    Nsim = int(Tsim / dt_plant)
    if not (Tsim / dt_plant).is_integer():
        print("WARNING: Tsim / dt_plant should be an integer!")

    # steady-state
    xs = np.array([[0.878, 324.5, 0.659]]).T
    us = np.array([[300, 0.1]]).T
    # initial state
    x0 = np.array([0.05, 0.75, 0.5]) * xs.ravel()
    # constant ref
    X_ref = np.tile(xs, Nsim + 1).T
    U_ref = np.tile(us, Nsim).T

    cstr_params = CSTRParameters()
    dist_params = DistCSTRParameters()
    mpc_params = MpcCSTRParameters(xs=cstr_params.xs, us=cstr_params.us)
    model = setup_cstr_model(cstr_params)
    plant_model = setup_cstr_model(cstr_params)

    integrator = setup_acados_integrator(plant_model, dt_plant, cstr_param=cstr_params)

    # Disturbance Generator
    dist_samples = samplesFromEllipsoid(Nsim, \
        np.zeros((plant_model.x.shape[0])), dist_params.W_mat)

    X_all = []
    U_all = []
    labels_all = []
    timings_solver_all = []

    # simulation with Nominal MPC
    label = "Nominal"
    print(f"\n\nRunning simulation with {label}\n\n")
    mpc_params.cstr_tightening = False
    ocp_solver = setup_acados_ocp_solver(model, mpc_params, cstr_params=cstr_params, dist_params=dist_params)

    ubx = cstr_params.xs * (1.0 + np.array([dist_params.c_exceed_ratio, dist_params.t_exceed_ratio, dist_params.h_exceed_ratio]))
    X, U, timings_solver, _ = simulate(
        ocp_solver, integrator, x0, Nsim, X_ref=X_ref, U_ref=U_ref, \
            cstr_tightening=False, dist_samples=dist_samples, ubx=ubx)
    X_all.append(X)
    U_all.append(U)
    timings_solver_all.append(timings_solver)
    labels_all.append(label)
    ocp_solver = None

    # simulation with zoRO MPC
    label = "fast zoRO"
    print(f"\n\nRunning simulation with {label}\n\n")
    mpc_params.cstr_tightening = True
    ocp_solver = setup_acados_ocp_solver(model, mpc_params, cstr_params=cstr_params, dist_params=dist_params)

    X, U, timings_solver, _ = simulate(
        ocp_solver, integrator, x0, Nsim, X_ref=X_ref, U_ref=U_ref, \
            cstr_tightening=True, dist_samples=dist_samples, ubx = ubx)
    X_all.append(X)
    U_all.append(U)
    timings_solver_all.append(timings_solver)
    labels_all.append(label)
    ocp_solver = None

    # Evaluation
    print("\nTiming evaluation:\n------------------")
    for i in range(len(labels_all)):
        label = labels_all[i]
        timings_solver = timings_solver_all[i] * 1e3
        print(
            f"{label}:\n min: {np.nanmin(timings_solver):.3f} ms, mean: {np.nanmean(timings_solver):.3f} ms, max: {np.nanmax(timings_solver):.3f} ms\n"
        )

    matplotlib.use("TkAgg")
    x_max = cstr_params.xs \
        * (1.0 + np.array([dist_params.c_exceed_ratio, dist_params.t_exceed_ratio, dist_params.h_exceed_ratio]))
    plot_cstr(
        dt_plant,
        X_all,
        U_all,
        X_ref,
        U_ref,
        mpc_params.umin,
        mpc_params.umax,
        labels_all,
        x_max = x_max
    )  # , fig_filename='cstr_acados_RTI.pdf')


if __name__ == "__main__":
    main()