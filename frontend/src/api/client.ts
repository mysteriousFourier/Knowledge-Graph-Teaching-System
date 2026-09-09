import axios, { type AxiosInstance } from "axios"
import { getRuntimeConfig } from "@/lib/config"

const config = getRuntimeConfig()

export const educationClient: AxiosInstance = axios.create({
  baseURL: config.educationApiBaseUrl,
  headers: { "Content-Type": "application/json" },
  timeout: 0,
})

export const maintenanceClient: AxiosInstance = axios.create({
  baseURL: config.maintenanceApiBaseUrl,
  headers: { "Content-Type": "application/json" },
})

educationClient.interceptors.request.use((request) => {
  request.baseURL = getRuntimeConfig().educationApiBaseUrl
  return request
})

maintenanceClient.interceptors.request.use((request) => {
  request.baseURL = getRuntimeConfig().maintenanceApiBaseUrl
  return request
})

// Request interceptor to add auth token
const addAuthToken = (client: AxiosInstance) => {
  client.interceptors.request.use(
    (config) => {
      const token = localStorage.getItem("kgts_token")
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
      return config
    },
    (error) => Promise.reject(error)
  )
}

addAuthToken(educationClient)
addAuthToken(maintenanceClient)

// Response interceptor for error handling
const handleError = (client: AxiosInstance) => {
  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response?.status === 401) {
        localStorage.removeItem("kgts_token")
        window.location.href = "/login"
      }
      return Promise.reject(error)
    }
  )
}

handleError(educationClient)
handleError(maintenanceClient)
