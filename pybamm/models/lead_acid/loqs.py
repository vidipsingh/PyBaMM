#
# Lead-acid LOQS model
#
import pybamm


class LOQS(pybamm.LeadAcidBaseModel):
    """Leading-Order Quasi-Static model for lead-acid, from [1]_.

    References
    ----------
    .. [1] V Sulzer, SJ Chapman, CP Please, DA Howey, and CW Monroe. Faster Lead-Acid
           Battery Simulations from Porous-Electrode Theory: II. Asymptotic Analysis.
           arXiv preprint arXiv:1902.01774, 2019.

    **Extends:** :class:`pybamm.LeadAcidBaseModel`
    """

    def __init__(self, options=None):
        super().__init__(options)
        self.name = "LOQS model"

        "-----------------------------------------------------------------------------"
        "Parameters"
        param = self.set_of_parameters
        i_boundary_cc = param.current_with_time

        "-----------------------------------------------------------------------------"
        "Model Variables"

        self.set_model_variables()
        c_e = self.variables["Electrolyte concentration"]

        "-----------------------------------------------------------------------------"
        "Model for current"

        # Exchange-current density
        neg = ["negative electrode"]
        pos = ["positive electrode"]
        int_curr_model = pybamm.interface.LeadAcidReaction(param)
        j0_n = int_curr_model.get_exchange_current_densities(c_e, neg)
        j0_p = int_curr_model.get_exchange_current_densities(c_e, pos)

        # Open-circuit potential
        ocp_n = param.U_n(c_e)
        ocp_p = param.U_p(c_e)

        if self.options["capacitance"] is not False:
            delta_phi_n = self.variables[
                "Negative electrode surface potential difference"
            ]
            delta_phi_p = self.variables[
                "Positive electrode surface potential difference"
            ]
            self.set_boundary_conditions(self.variables)
            i_boundary_cc = self.variables["Current collector current density"]

            # Reaction overpotential
            eta_r_n = delta_phi_n - ocp_n
            eta_r_p = delta_phi_p - ocp_p

            # Interfacial current density
            j_n = int_curr_model.get_butler_volmer(j0_n, eta_r_n, neg)
            j_p = int_curr_model.get_butler_volmer(j0_p, eta_r_p, pos)

            # Porosity
            self.set_porosity_model(j_n, j_p)

            # Electrolyte current
            eleclyte_current_model = pybamm.electrolyte_current.MacInnesCapacitance(
                param, self.options["capacitance"]
            )
            eleclyte_current_model.set_leading_order_system(
                self.variables, self.reactions, neg
            )
            eleclyte_current_model.set_leading_order_system(
                self.variables, self.reactions, pos
            )
            self.update(eleclyte_current_model)

        else:
            i_boundary_cc = param.current_with_time
            self.variables["Current collector current density"] = i_boundary_cc

            # Interfacial current density
            j_n = int_curr_model.get_homogeneous_interfacial_current(i_boundary_cc, neg)
            j_p = int_curr_model.get_homogeneous_interfacial_current(i_boundary_cc, pos)

            # Porosity
            self.set_porosity_model(j_n, j_p)

            # Potentials
            eta_r_n = int_curr_model.get_inverse_butler_volmer(j_n, j0_n, neg)
            eta_r_p = int_curr_model.get_inverse_butler_volmer(j_p, j0_p, pos)

            eleclyte_current_model = pybamm.electrolyte_current.MacInnesStefanMaxwell(
                param
            )

        eleclyte_conc_model = pybamm.electrolyte_diffusion.StefanMaxwell(param)
        eleclyte_conc_model.set_leading_order_system(self.variables, self.reactions)

        self.update(eleclyte_conc_model)

        "-----------------------------------------------------------------------------"
        "Post-Processing"

        # Exchange-current density
        j_vars = int_curr_model.get_derived_interfacial_currents(j_n, j_p, j0_n, j0_p)
        self.variables.update(j_vars)

        # Potentials
        pot_model = pybamm.potential.Potential(param)
        ocp_vars = pot_model.get_derived_open_circuit_potentials(ocp_n, ocp_p)
        eta_r_vars = pot_model.get_derived_reaction_overpotentials(eta_r_n, eta_r_p)
        self.variables.update({**ocp_vars, **eta_r_vars})

        # Electrolyte current
        elyte_vars = eleclyte_current_model.get_explicit_leading_order(self.variables)
        self.variables.update(elyte_vars)

        # Electrode
        electrode_model = pybamm.electrode.Ohm(param)
        electrode_vars = electrode_model.get_explicit_leading_order(self.variables)
        self.variables.update(electrode_vars)

        # Cut-off voltage
        # Hack to extract voltage at the tabs in 2D
        voltage = self.variables["Terminal voltage"]
        voltage.domain = c_e.domain
        voltage = pybamm.boundary_value(voltage, "right")
        self.events.append(voltage - param.voltage_low_cut)

        "-----------------------------------------------------------------------------"
        "Settings"
        # ODEs only (don't use jacobian, use base spatial method)
        self.use_jacobian = False

    def set_model_variables(self):
        "Set variables for the model"
        if self.options["bc_options"]["dimensionality"] == 0:
            curr_coll_domain = []
        elif self.options["bc_options"]["dimensionality"] == 1:
            curr_coll_domain = ["current collector"]

        c_e = pybamm.Variable("Electrolyte concentration", curr_coll_domain)

        # Piecewise constant epsilon
        eps_n_pc = pybamm.Variable("Negative electrode porosity", curr_coll_domain)
        eps_s_pc = pybamm.Variable("Separator porosity", curr_coll_domain)
        eps_p_pc = pybamm.Variable("Positive electrode porosity", curr_coll_domain)

        epsilon = pybamm.Concatenation(
            pybamm.Broadcast(eps_n_pc, ["negative electrode"]),
            pybamm.Broadcast(eps_s_pc, ["separator"]),
            pybamm.Broadcast(eps_p_pc, ["positive electrode"]),
        )

        self.variables.update({"Electrolyte concentration": c_e, "Porosity": epsilon})

        if self.options["capacitance"] is not False:
            delta_phi_n = pybamm.Variable(
                "Negative electrode surface potential difference", curr_coll_domain
            )
            delta_phi_p = pybamm.Variable(
                "Positive electrode surface potential difference", curr_coll_domain
            )
            self.variables.update(
                {
                    "Negative electrode surface potential difference": delta_phi_n,
                    "Positive electrode surface potential difference": delta_phi_p,
                }
            )

    def set_boundary_conditions(self, bc_variables=None):
        "Set boundary conditions, dependent on self.options"
        # TODO: edit to allow constant-current and constant-power control
        param = self.set_of_parameters
        dimensionality = self.options["bc_options"]["dimensionality"]
        if dimensionality == 0:
            current_bc = param.current_with_time
            self.variables["Current collector current density"] = current_bc
        elif dimensionality == 1:
            current_collector_model = pybamm.vertical.Vertical(param)
            current_collector_model.set_leading_order_vertical_current(bc_variables)
            self.update(current_collector_model)

    def set_porosity_model(self, j_n, j_p):
        " Set model for porosity"
        # Porosity
        param = self.set_of_parameters
        porosity_model = pybamm.porosity.Standard(param)
        epsilon = self.variables["Porosity"]
        porosity_model.set_leading_order_system(epsilon, j_n, j_p)
        self.update(porosity_model)

        # Electrolyte concentration
        por_vars = porosity_model.variables
        deps_n_dt = por_vars["Negative electrode porosity change"].orphans[0]
        deps_p_dt = por_vars["Positive electrode porosity change"].orphans[0]
        self.reactions = {
            "main": {
                "neg": {"s_plus": param.s_n, "aj": j_n, "deps_dt": deps_n_dt},
                "pos": {"s_plus": param.s_p, "aj": j_p, "deps_dt": deps_p_dt},
            }
        }

    @property
    def default_spatial_methods(self):
        # ODEs only in the macroscale, so use base spatial method
        return {
            "macroscale": pybamm.FiniteVolume,
            "current collector": pybamm.FiniteVolume,
        }

    @property
    def default_geometry(self):
        if self.options["bc_options"]["dimensionality"] == 0:
            return pybamm.Geometry("1D macro")
        elif self.options["bc_options"]["dimensionality"] == 1:
            return pybamm.Geometry("1+1D macro")

    @property
    def default_solver(self):
        """
        Create and return the default solver for this model
        """
        # Different solver depending on whether we solve ODEs or DAEs
        if self.options["capacitance"] == "algebraic":
            return pybamm.ScikitsDaeSolver()
        else:
            return pybamm.ScikitsOdeSolver()
