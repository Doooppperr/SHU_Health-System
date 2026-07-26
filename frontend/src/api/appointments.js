import http from "./http";

export const fetchAppointmentAvailability = (appointmentDate, q = "") => http.get("/appointments/availability", { params: { appointment_date: appointmentDate, q } });
export const fetchMyAppointments = () => http.get("/appointments");
export const createAppointment = (payload) => http.post("/appointments", payload);
export const cancelAppointment = (id) => http.post(`/appointments/${id}/cancel`);
export const fetchBookingGroups = (params = {}) => http.get("/booking-groups", { params });
export const createBookingGroup = (payload) => http.post("/booking-groups", payload);
export const cancelBookingGroup = (id) => http.post(`/booking-groups/${id}/cancel`);
export const fetchBookingIntakeDefaults = () => http.get("/booking-intake-defaults");
export const fetchWaitlistSubscriptions = (params = {}) => http.get("/waitlist-subscriptions", { params });
export const createWaitlistSubscription = (payload) => http.post("/waitlist-subscriptions", payload);
export const cancelWaitlistSubscription = (id) => http.delete(`/waitlist-subscriptions/${id}`);
